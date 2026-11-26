from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EvidenceType,
    Product,
    Scan,
    ScanEvidence,
    ScanResult,
    ScanStatus,
    VerificationStatus,
)
from app.services.halal_engine import (
    ANALYSIS_VERSION,
    CertificationEvidenceInput,
    HalalAssessmentResult,
    halal_analyzer,
)


def _serialize_classification(item: object) -> dict[str, object]:
    record = getattr(item, "record", None)
    normalized = getattr(item, "normalized")
    evidence = getattr(item, "evidence", ())
    return {
        "original_name": normalized.original_text,
        "original_text": normalized.original_text,
        "normalized_name": normalized.normalized_name,
        "e_number": normalized.e_number,
        "ins_number": normalized.ins_number,
        "status": item.status.value,
        "reason": item.reason,
        "match_type": item.match_type,
        "matched_record_id": str(record.id) if record is not None else None,
        "canonical_name": record.canonical_name if record is not None else item.matched_alias,
        "matched_alias": item.matched_alias,
        "review_status": item.review_status.value if item.review_status is not None else None,
        "source_classification": record.source_classification if record is not None else None,
        "confidence": item.confidence,
        "warnings": list(item.warnings),
        "evidence": [
            {
                "source_name": ref.source_name,
                "source_url": ref.source_url,
                "evidence_summary": ref.evidence_summary,
                "evidence_type": ref.evidence_type.value,
                "reviewer_or_organization": ref.reviewer_or_organization,
                "verification_date": (
                    ref.verification_date.isoformat() if ref.verification_date else None
                ),
                "review_status": ref.review_status.value,
            }
            for ref in evidence
        ],
    }


def analysis_snapshot(
    result: ScanResult | None,
    assessment: HalalAssessmentResult | None = None,
) -> dict[str, object]:
    if result is None and assessment is None:
        return {
            "overall_status": "UNKNOWN",
            "verdict": "insufficient_information",
            "confidence_score": None,
            "explanation": "Analysis is not available.",
            "analysis_version": ANALYSIS_VERSION,
            "analyzed_at": None,
            "warnings": [],
            "ingredients_analysis": [],
            "certifications": [],
        }

    if assessment is not None:
        return {
            "overall_status": assessment.legacy_status.value,
            "verdict": assessment.verdict.value,
            "confidence_score": assessment.confidence_score,
            "explanation": assessment.explanation,
            "analysis_version": assessment.analysis_version,
            "analyzed_at": assessment.analyzed_at.isoformat(),
            "warnings": list(assessment.warnings),
            "ingredients_analysis": [
                _serialize_classification(item) for item in assessment.classifications
            ],
            "certifications": [],
        }

    assert result is not None
    return {
        "overall_status": result.overall_status.value,
        "verdict": "insufficient_information",
        "confidence_score": result.confidence_score,
        "explanation": result.explanation,
        "analysis_version": result.analysis_version,
        "analyzed_at": result.created_at.isoformat() if result.created_at else None,
        "warnings": [],
        "ingredients_analysis": [],
        "certifications": [],
    }


def build_snapshot(
    *,
    product: object | None = None,
    barcode: str | None = None,
    result: ScanResult | None = None,
    assessment: HalalAssessmentResult | None = None,
    original_ingredients_text: str | None = None,
    ingredients: list[object] | None = None,
    certifications: list[object] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    product_data = getattr(product, "external_data", {}) or {}
    product_id = getattr(product, "id", None)
    product_snapshot: dict[str, object] = {
        "id": str(product_id) if product_id is not None else None,
        "name": getattr(product, "name", None),
        "brand": getattr(product, "brand", None),
        "barcode": getattr(product, "barcode", barcode),
        "image_url": getattr(product, "image_url", None),
        "description": getattr(product, "description", None),
        "categories": product_data.get("categories", []) if isinstance(product_data, dict) else [],
        "country_of_origin": getattr(product, "country_of_origin", None),
        "ingredients": ingredients or [],
        "ingredients_text": (
            product_data.get("ingredients_text")
            if isinstance(product_data, dict)
            else original_ingredients_text
        ) or original_ingredients_text,
        "nutriments": product_data.get("nutriments", {}) if isinstance(product_data, dict) else {},
        "labels": product_data.get("labels", []) if isinstance(product_data, dict) else [],
        "source": getattr(product, "source", None),
    }
    analysis = analysis_snapshot(result, assessment=assessment)
    if warnings:
        analysis["warnings"] = warnings
    analysis["certifications"] = certifications or []
    return {"product": product_snapshot, "analysis": analysis}


async def analyze_scan(
    db: AsyncSession,
    scan: Scan,
    *,
    original_ingredients_text: str | None,
    detected_language: str | None,
    ingredients_list: list[str] | None = None,
    certifications: list[CertificationEvidenceInput] | None = None,
    return_assessment: bool = False,
) -> ScanResult | tuple[ScanResult, HalalAssessmentResult]:
    if original_ingredients_text is not None:
        scan.original_ingredients_text = original_ingredients_text
    if detected_language is not None:
        scan.detected_language = detected_language

    assessment = await halal_analyzer.analyze(
        db,
        ingredients_text=scan.original_ingredients_text,
        ingredients_list=ingredients_list,
        certifications=certifications,
    )

    scan.scan_status = ScanStatus.COMPLETED
    scan.analysis_completed_at = assessment.analyzed_at
    result = await db.scalar(
        select(ScanResult)
        .where(ScanResult.scan_id == scan.id)
        .order_by(ScanResult.created_at.desc())
        .limit(1)
    )

    evidence_rows: list[ScanEvidence] = []
    for item in assessment.classifications:
        record = item.record
        first_ref = item.evidence[0] if item.evidence else None
        evidence_rows.append(
            ScanEvidence(
                ingredient_id=record.id if record is not None else None,
                evidence_type=(
                    first_ref.evidence_type if first_ref is not None else EvidenceType.INGREDIENT
                ),
                evidence_description=item.reason,
                source_name=first_ref.source_name if first_ref is not None else "HalalLens Engine",
                source_url=first_ref.source_url if first_ref is not None else None,
                verification_status=(
                    VerificationStatus.VERIFIED
                    if first_ref is not None and first_ref.review_status.value == "verified"
                    else VerificationStatus.UNVERIFIED
                ),
            )
        )

    if not evidence_rows:
        evidence_rows.append(
            ScanEvidence(
                evidence_type=EvidenceType.OTHER,
                evidence_description=assessment.explanation,
                source_name="HalalLens Engine",
                verification_status=VerificationStatus.UNVERIFIED,
            )
        )

    if result is None:
        result = ScanResult(
            scan_id=scan.id,
            overall_status=assessment.legacy_status,
            confidence_score=assessment.confidence_score,
            explanation=assessment.explanation,
            analysis_version=assessment.analysis_version,
            evidence=evidence_rows,
        )
        db.add(result)
        await db.flush()
    else:
        result.overall_status = assessment.legacy_status
        result.confidence_score = assessment.confidence_score
        result.explanation = assessment.explanation
        result.analysis_version = assessment.analysis_version

    product = None
    if scan.product_id is not None:
        product = await db.get(Product, scan.product_id)
    scan.snapshot = build_snapshot(
        product=product,
        barcode=scan.barcode,
        result=result,
        assessment=assessment,
        original_ingredients_text=scan.original_ingredients_text,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    if return_assessment:
        return result, assessment
    return result


async def load_scan(db: AsyncSession, scan_id: UUID) -> Scan | None:
    return await db.get(Scan, scan_id)
