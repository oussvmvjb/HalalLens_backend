from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    EvidenceReviewStatus,
    EvidenceType,
    IngredientHalalStatus,
    ProductHalalVerdict,
)


class HalalCertificationInput(BaseModel):
    issuer: str = Field(min_length=1, max_length=255)
    is_recognized_issuer: bool = False
    is_verified: bool = False
    is_expired: bool = False
    certificate_number: str | None = Field(default=None, max_length=120)


class HalalAnalyzeProductRequest(BaseModel):
    barcode: str | None = Field(default=None, max_length=64)
    product_name: str | None = Field(default=None, max_length=255)
    ingredients_text: str | None = Field(default=None, max_length=20000)
    ingredients: list[str] = Field(default_factory=list, max_length=200)
    product_source: str | None = Field(default=None, max_length=120)
    ocr_text: str | None = Field(default=None, max_length=20000)
    certifications: list[HalalCertificationInput] = Field(default_factory=list)
    use_ai_assistance: bool = False


class HalalProductInfo(BaseModel):
    barcode: str | None = None
    product_name: str | None = None
    product_source: str | None = None


class NormalizedIngredientResponse(BaseModel):
    original_text: str
    normalized_name: str
    e_number: str | None = None
    ins_number: str | None = None
    source_qualifier: str | None = None


class EvidenceReferenceResponse(BaseModel):
    source_name: str
    source_url: str | None = None
    evidence_summary: str
    evidence_type: EvidenceType
    reviewer_or_organization: str | None = None
    verification_date: date | None = None
    review_status: EvidenceReviewStatus


class MatchedDatabaseRecordResponse(BaseModel):
    id: UUID
    canonical_name: str
    normalized_name: str
    ingredient_type: str | None = None
    e_number: str | None = None
    ins_number: str | None = None
    halal_status: IngredientHalalStatus
    source_classification: str | None = None
    source_requirements: str | None = None
    confidence_level: float


class IngredientClassificationResponse(BaseModel):
    original_text: str
    normalized_name: str
    e_number: str | None = None
    ins_number: str | None = None
    status: IngredientHalalStatus
    reason: str
    match_type: str | None = None
    matched_alias: str | None = None
    matched_record_id: UUID | None = None
    canonical_name: str | None = None
    source_classification: str | None = None
    review_status: EvidenceReviewStatus | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReferenceResponse] = Field(default_factory=list)


class HalalAnalyzeProductResponse(BaseModel):
    success: Literal[True] = True
    product: HalalProductInfo
    normalized_ingredients: list[NormalizedIngredientResponse] = Field(default_factory=list)
    ingredient_classifications: list[IngredientClassificationResponse] = Field(default_factory=list)
    matched_records: list[MatchedDatabaseRecordResponse] = Field(default_factory=list)
    evidence_references: list[EvidenceReferenceResponse] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verdict: ProductHalalVerdict
    explanation: str
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    certification_verified: bool = False
    ai_assisted: bool = False
    manual_review_required: bool = False
    analysis_version: str
    analyzed_at: datetime
