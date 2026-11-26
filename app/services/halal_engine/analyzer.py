from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.halal_engine.ai_assistant import OptionalAIAssistant
from app.services.halal_engine.ingredient_matcher import IngredientMatcher
from app.services.halal_engine.knowledge_base import DatabaseKnowledgeBase
from app.services.halal_engine.models import (
    CertificationEvidenceInput,
    EvidenceReference,
    HalalAssessmentResult,
)
from app.services.halal_engine.normalizer import split_and_normalize_ingredients
from app.services.halal_engine.rules import (
    determine_product_verdict,
    evaluate_ingredient,
)

ANALYSIS_VERSION = "halal-engine-v1"


class HalalIngredientAnalyzer:
    """Orchestrates normalization, database matching, and deterministic halal rules."""

    def __init__(
        self,
        knowledge_base: DatabaseKnowledgeBase | None = None,
        ai_assistant: OptionalAIAssistant | None = None,
    ) -> None:
        self._kb = knowledge_base or DatabaseKnowledgeBase()
        self._ai = ai_assistant or OptionalAIAssistant()

    async def analyze(
        self,
        db: AsyncSession,
        *,
        ingredients_text: str | None = None,
        ingredients_list: list[str] | None = None,
        ocr_text: str | None = None,
        certifications: list[CertificationEvidenceInput] | None = None,
        use_ai_assistance: bool = False,
    ) -> HalalAssessmentResult:
        ai_assisted = False
        manual_review_required = False
        effective_list = list(ingredients_list or [])
        effective_text = ingredients_text

        if not effective_list and not (effective_text and effective_text.strip()) and ocr_text:
            if use_ai_assistance:
                extracted = await self._ai.extract_ingredients_from_ocr(ocr_text)
                if extracted is not None and extracted.ingredients:
                    effective_list = extracted.ingredients
                    ai_assisted = True
                    manual_review_required = extracted.manual_review_required
            if not effective_list:
                effective_text = ocr_text

        normalized_items = split_and_normalize_ingredients(
            ingredients_text=effective_text,
            ingredients_list=effective_list,
        )

        records = await self._kb.load_ingredients(db)
        active_rules = await self._kb.load_active_rules(db)
        matcher = IngredientMatcher(records)

        classifications = []
        evidence_refs: list[EvidenceReference] = []
        seen_evidence: set[tuple[str, str]] = set()

        for item in normalized_items:
            record, match_type = matcher.match(item)
            matched = evaluate_ingredient(item, record, match_type)
            classifications.append(matched)
            for ref in matched.evidence:
                key = (ref.source_name, ref.evidence_summary)
                if key not in seen_evidence:
                    seen_evidence.add(key)
                    evidence_refs.append(ref)

        verdict, legacy_status, explanation, confidence, cert_verified, warnings = (
            determine_product_verdict(
                classifications,
                certifications or [],
                active_rules=active_rules,
            )
        )

        if any(c.status.value == "unknown" for c in classifications):
            manual_review_required = True

        return HalalAssessmentResult(
            verdict=verdict,
            legacy_status=legacy_status,
            explanation=explanation,
            confidence_score=confidence,
            normalized_ingredients=tuple(normalized_items),
            classifications=tuple(classifications),
            evidence_references=tuple(evidence_refs),
            warnings=tuple(warnings),
            certification_verified=cert_verified,
            analysis_version=ANALYSIS_VERSION,
            analyzed_at=datetime.now(timezone.utc),
            ai_assisted=ai_assisted,
            manual_review_required=manual_review_required,
        )
