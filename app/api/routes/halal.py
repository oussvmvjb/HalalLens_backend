import logging

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies.auth import DatabaseSession, OptionalUser
from app.schemas.halal import (
    EvidenceReferenceResponse,
    HalalAnalyzeProductRequest,
    HalalAnalyzeProductResponse,
    HalalProductInfo,
    IngredientClassificationResponse,
    MatchedDatabaseRecordResponse,
    NormalizedIngredientResponse,
)
from app.services.halal_engine import (
    CertificationEvidenceInput,
    KnowledgeBaseUnavailableError,
    halal_analyzer,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/halal", tags=["Halal analysis"])


@router.post("/analyze-product", response_model=HalalAnalyzeProductResponse)
async def analyze_product(
    payload: HalalAnalyzeProductRequest,
    db: DatabaseSession,
    user: OptionalUser,
) -> HalalAnalyzeProductResponse:
    _ = user  # Optional authentication supported consistently with scan endpoints
    certifications = [
        CertificationEvidenceInput(
            issuer=cert.issuer,
            is_recognized_issuer=cert.is_recognized_issuer,
            is_verified=cert.is_verified,
            is_expired=cert.is_expired,
            certificate_number=cert.certificate_number,
        )
        for cert in payload.certifications
    ]

    try:
        result = await halal_analyzer.analyze(
            db,
            ingredients_text=payload.ingredients_text,
            ingredients_list=payload.ingredients,
            ocr_text=payload.ocr_text,
            certifications=certifications,
            use_ai_assistance=payload.use_ai_assistance,
        )
    except KnowledgeBaseUnavailableError as error:
        logger.exception("Halal knowledge base is unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ingredient knowledge base is temporarily unavailable.",
        ) from error

    matched_records_by_id: dict[str, MatchedDatabaseRecordResponse] = {}
    classifications: list[IngredientClassificationResponse] = []
    for item in result.classifications:
        record = item.record
        if record is not None and str(record.id) not in matched_records_by_id:
            matched_records_by_id[str(record.id)] = MatchedDatabaseRecordResponse(
                id=record.id,
                canonical_name=record.canonical_name,
                normalized_name=record.normalized_name,
                ingredient_type=record.ingredient_type,
                e_number=record.e_number,
                ins_number=record.ins_number,
                halal_status=record.halal_status,
                source_classification=record.source_classification,
                source_requirements=record.source_requirements,
                confidence_level=record.confidence_level,
            )
        classifications.append(
            IngredientClassificationResponse(
                original_text=item.normalized.original_text,
                normalized_name=item.normalized.normalized_name,
                e_number=item.normalized.e_number,
                ins_number=item.normalized.ins_number,
                status=item.status,
                reason=item.reason,
                match_type=item.match_type,
                matched_alias=item.matched_alias,
                matched_record_id=record.id if record is not None else None,
                canonical_name=record.canonical_name if record is not None else item.matched_alias,
                source_classification=record.source_classification if record is not None else None,
                review_status=item.review_status,
                confidence=item.confidence,
                warnings=list(item.warnings),
                evidence=[
                    EvidenceReferenceResponse(
                        source_name=ref.source_name,
                        source_url=ref.source_url,
                        evidence_summary=ref.evidence_summary,
                        evidence_type=ref.evidence_type,
                        reviewer_or_organization=ref.reviewer_or_organization,
                        verification_date=ref.verification_date,
                        review_status=ref.review_status,
                    )
                    for ref in item.evidence
                ],
            )
        )

    return HalalAnalyzeProductResponse(
        product=HalalProductInfo(
            barcode=payload.barcode,
            product_name=payload.product_name,
            product_source=payload.product_source,
        ),
        normalized_ingredients=[
            NormalizedIngredientResponse(
                original_text=item.original_text,
                normalized_name=item.normalized_name,
                e_number=item.e_number,
                ins_number=item.ins_number,
                source_qualifier=item.source_qualifier,
            )
            for item in result.normalized_ingredients
        ],
        ingredient_classifications=classifications,
        matched_records=list(matched_records_by_id.values()),
        evidence_references=[
            EvidenceReferenceResponse(
                source_name=ref.source_name,
                source_url=ref.source_url,
                evidence_summary=ref.evidence_summary,
                evidence_type=ref.evidence_type,
                reviewer_or_organization=ref.reviewer_or_organization,
                verification_date=ref.verification_date,
                review_status=ref.review_status,
            )
            for ref in result.evidence_references
        ],
        warnings=list(result.warnings),
        verdict=result.verdict,
        explanation=result.explanation,
        confidence_score=result.confidence_score,
        certification_verified=result.certification_verified,
        ai_assisted=result.ai_assisted,
        manual_review_required=result.manual_review_required,
        analysis_version=result.analysis_version,
        analyzed_at=result.analyzed_at,
    )
