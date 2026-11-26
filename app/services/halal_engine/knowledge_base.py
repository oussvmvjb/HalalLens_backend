from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import HalalRule, Ingredient
from app.services.halal_engine.exceptions import KnowledgeBaseUnavailableError
from app.services.halal_engine.models import (
    EvidenceReference,
    HalalRuleRecord,
    IngredientRecord,
)


class DatabaseKnowledgeBase:
    """Database-backed ingredient, evidence, and halal rule repository."""

    async def load_ingredients(self, db: AsyncSession) -> list[IngredientRecord]:
        try:
            rows = (
                await db.scalars(
                    select(Ingredient).options(
                        selectinload(Ingredient.sources),
                        selectinload(Ingredient.translations),
                    )
                )
            ).all()
        except SQLAlchemyError as error:
            raise KnowledgeBaseUnavailableError(
                "Failed to query ingredient knowledge base."
            ) from error

        records: list[IngredientRecord] = []
        for row in rows:
            multilingual: list[str] = []
            for translation in row.translations:
                if translation.translated_name:
                    multilingual.append(translation.translated_name)
                if isinstance(translation.aliases, list):
                    multilingual.extend(
                        alias for alias in translation.aliases if isinstance(alias, str)
                    )

            sources = tuple(
                EvidenceReference(
                    source_name=src.source_name,
                    source_url=src.source_url,
                    evidence_summary=src.evidence_summary,
                    evidence_type=src.evidence_type,
                    reviewer_or_organization=src.reviewer_or_organization,
                    verification_date=src.verification_date,
                    review_status=src.review_status,
                    imported_at=src.imported_at,
                )
                for src in row.sources
            )

            records.append(
                IngredientRecord(
                    id=row.id,
                    canonical_name=row.canonical_name,
                    normalized_name=row.normalized_name,
                    ingredient_type=row.ingredient_type,
                    e_number=row.e_number,
                    ins_number=row.ins_number,
                    aliases=tuple(
                        alias for alias in (row.aliases or []) if isinstance(alias, str)
                    ),
                    multilingual_names=tuple(multilingual),
                    description=row.description,
                    halal_status=row.halal_status,
                    source_requirements=row.source_requirements,
                    conditions=tuple(
                        cond for cond in (row.conditions or []) if isinstance(cond, str)
                    ),
                    confidence_level=float(row.confidence_level or 0.0),
                    source_classification=row.source_classification,
                    sources=sources,
                )
            )
        return records

    async def load_active_rules(self, db: AsyncSession) -> list[HalalRuleRecord]:
        try:
            rows = (
                await db.scalars(
                    select(HalalRule)
                    .where(HalalRule.active.is_(True))
                    .order_by(HalalRule.rule_priority.asc())
                )
            ).all()
        except SQLAlchemyError as error:
            raise KnowledgeBaseUnavailableError(
                "Failed to query active halal rules."
            ) from error

        return [
            HalalRuleRecord(
                rule_name=row.rule_name,
                rule_description=row.rule_description,
                rule_priority=row.rule_priority,
                applicable_conditions=dict(row.applicable_conditions or {}),
                rule_version=row.rule_version,
            )
            for row in rows
        ]
