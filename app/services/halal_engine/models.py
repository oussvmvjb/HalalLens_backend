from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from app.models.enums import (
    EvidenceReviewStatus,
    EvidenceType,
    HalalStatus,
    IngredientHalalStatus,
    ProductHalalVerdict,
)


@dataclass(frozen=True)
class NormalizedIngredient:
    original_text: str
    normalized_name: str
    e_number: str | None = None
    ins_number: str | None = None
    source_qualifier: str | None = None


@dataclass(frozen=True)
class EvidenceReference:
    source_name: str
    source_url: str | None
    evidence_summary: str
    evidence_type: EvidenceType
    reviewer_or_organization: str | None
    verification_date: date | None
    review_status: EvidenceReviewStatus
    imported_at: datetime | None = None


@dataclass(frozen=True)
class IngredientRecord:
    id: UUID
    canonical_name: str
    normalized_name: str
    ingredient_type: str | None
    e_number: str | None
    ins_number: str | None
    aliases: tuple[str, ...]
    multilingual_names: tuple[str, ...]
    description: str | None
    halal_status: IngredientHalalStatus
    source_requirements: str | None
    conditions: tuple[str, ...]
    confidence_level: float
    source_classification: str | None = None
    sources: tuple[EvidenceReference, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HalalRuleRecord:
    rule_name: str
    rule_description: str
    rule_priority: int
    applicable_conditions: dict[str, object]
    rule_version: str


@dataclass(frozen=True)
class MatchedIngredient:
    normalized: NormalizedIngredient
    record: IngredientRecord | None
    match_type: str | None  # "exact", "e_number", "ins_number", "alias", "translation", or None
    status: IngredientHalalStatus
    reason: str
    matched_alias: str | None = None
    review_status: EvidenceReviewStatus | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[EvidenceReference, ...] = field(default_factory=tuple)
    confidence: float = 0.0


@dataclass(frozen=True)
class CertificationEvidenceInput:
    issuer: str
    is_recognized_issuer: bool
    is_verified: bool
    is_expired: bool = False
    certificate_number: str | None = None


@dataclass(frozen=True)
class HalalAssessmentResult:
    verdict: ProductHalalVerdict
    legacy_status: HalalStatus
    explanation: str
    confidence_score: float | None
    normalized_ingredients: tuple[NormalizedIngredient, ...]
    classifications: tuple[MatchedIngredient, ...]
    evidence_references: tuple[EvidenceReference, ...]
    warnings: tuple[str, ...]
    certification_verified: bool
    analysis_version: str
    analyzed_at: datetime
    ai_assisted: bool = False
    manual_review_required: bool = False
