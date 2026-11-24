from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    EvidenceType,
    HalalStatus,
    Product,
    Scan,
    ScanEvidence,
    ScanResult,
    ScanStatus,
    VerificationStatus,
)


def analysis_snapshot(result: ScanResult | None) -> dict[str, object]:
    if result is None:
        return {
            "overall_status": "UNKNOWN",
            "confidence_score": None,
            "explanation": "Analysis is not available.",
            "analysis_version": "foundation-v1",
            "warnings": [],
            "ingredients_analysis": [],
            "certifications": [],
        }
    return {
        "overall_status": result.overall_status.value,
        "confidence_score": result.confidence_score,
        "explanation": result.explanation,
        "analysis_version": result.analysis_version,
        "warnings": [],
        "ingredients_analysis": [],
        "certifications": [],
    }


def build_snapshot(
    *,
    product: object | None = None,
    barcode: str | None = None,
    result: ScanResult | None = None,
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
    analysis = analysis_snapshot(result)
    analysis["warnings"] = warnings or []
    analysis["certifications"] = certifications or []
    return {"product": product_snapshot, "analysis": analysis}


async def analyze_scan(
    db: AsyncSession,
    scan: Scan,
    *,
    original_ingredients_text: str | None,
    detected_language: str | None,
) -> ScanResult:
    if original_ingredients_text is not None:
        scan.original_ingredients_text = original_ingredients_text
    if detected_language is not None:
        scan.detected_language = detected_language
    scan.scan_status = ScanStatus.COMPLETED
    scan.analysis_completed_at = datetime.now(timezone.utc)
    result = await db.scalar(
        select(ScanResult).where(ScanResult.scan_id == scan.id).order_by(ScanResult.created_at.desc()).limit(1)
    )
    if result is None:
        result = ScanResult(
            scan_id=scan.id,
            overall_status=HalalStatus.UNKNOWN,
            confidence_score=None,
            explanation=(
                "Automated ingredient and certification analysis is not enabled. "
                "No religious ruling was generated; verify the product with trusted sources."
            ),
            analysis_version="foundation-v1",
            evidence=[
                ScanEvidence(
                    evidence_type=EvidenceType.OTHER,
                    evidence_description=(
                        "Analysis service is a placeholder; no ingredient or certification evidence "
                        "was evaluated."
                    ),
                    source_name="HalalLens foundation",
                    verification_status=VerificationStatus.UNVERIFIED,
                )
            ],
        )
        db.add(result)
        await db.flush()
    else:
        result.explanation = (
            "Automated ingredient and certification analysis is not enabled. "
            "No religious ruling was generated; verify the product with trusted sources."
        )
    product = None
    if scan.product_id is not None:
        product = await db.get(Product, scan.product_id)
    scan.snapshot = build_snapshot(
        product=product,
        barcode=scan.barcode,
        result=result,
        original_ingredients_text=scan.original_ingredients_text,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    return result


async def load_scan(db: AsyncSession, scan_id: UUID) -> Scan | None:
    return await db.get(Scan, scan_id)
