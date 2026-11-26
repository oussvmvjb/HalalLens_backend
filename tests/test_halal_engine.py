from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from httpx import AsyncClient, MockTransport, Response
from sqlalchemy.exc import OperationalError

from app.models import (
    EvidenceReviewStatus,
    EvidenceType,
    IngredientHalalStatus,
    ProductHalalVerdict,
)
from app.services.halal_engine import (
    CertificationEvidenceInput,
    DatabaseKnowledgeBase,
    EvidenceReference,
    HalalIngredientAnalyzer,
    IngredientRecord,
    KnowledgeBaseUnavailableError,
    OptionalAIAssistant,
    split_and_normalize_ingredients,
)


def _record(
    canonical_name: str,
    status: IngredientHalalStatus,
    *,
    e_number: str | None = None,
    ins_number: str | None = None,
    aliases: tuple[str, ...] = (),
    multilingual_names: tuple[str, ...] = (),
    source_requirements: str | None = None,
    review_status: EvidenceReviewStatus = EvidenceReviewStatus.VERIFIED,
    include_source: bool = True,
) -> IngredientRecord:
    sources = (
        (
            EvidenceReference(
                source_name="Halal Standards Reference",
                source_url="https://example.org/halal-ref",
                evidence_summary=f"Reviewed classification for {canonical_name}.",
                evidence_type=EvidenceType.INGREDIENT,
                reviewer_or_organization="Halal Review Board",
                verification_date=date(2025, 1, 15),
                review_status=review_status,
            ),
        )
        if include_source
        else ()
    )
    return IngredientRecord(
        id=uuid4(),
        canonical_name=canonical_name,
        normalized_name=canonical_name.lower(),
        ingredient_type="additive" if e_number or ins_number else "general",
        e_number=e_number,
        ins_number=ins_number,
        aliases=aliases,
        multilingual_names=multilingual_names,
        description=f"{canonical_name} reference description",
        halal_status=status,
        source_requirements=source_requirements,
        conditions=(),
        confidence_level=0.9 if include_source else 0.0,
        sources=sources,
    )


class _StubKnowledgeBase(DatabaseKnowledgeBase):
    def __init__(self, records: list[IngredientRecord]) -> None:
        self._records = records

    async def load_ingredients(self, db):
        return self._records

    async def load_active_rules(self, db):
        return []


class _FailingKnowledgeBase(DatabaseKnowledgeBase):
    async def load_ingredients(self, db):
        raise KnowledgeBaseUnavailableError("Database connection lost")


def test_normalizer_handles_unicode_punctuation_and_codes() -> None:
    items = split_and_normalize_ingredients(
        ingredients_text="  Water (100%),  E 120 , INS-471 (vegetable) ; 甘油 ،  "
    )
    assert len(items) == 4
    assert items[0].normalized_name == "water"
    assert items[1].e_number == "E120"
    assert items[2].ins_number == "INS471"
    assert items[2].source_qualifier == "vegetable"
    assert items[3].normalized_name == "甘油"


@pytest.mark.asyncio
async def test_known_halal_ingredients_without_and_with_certification() -> None:
    kb = _StubKnowledgeBase(
        [
            _record("Water", IngredientHalalStatus.HALAL, aliases=("aqua",), multilingual_names=("水",)),
            _record("Sugar", IngredientHalalStatus.HALAL, multilingual_names=("سكر",)),
        ]
    )
    analyzer = HalalIngredientAnalyzer(knowledge_base=kb)

    without_cert = await analyzer.analyze(
        None,  # type: ignore[arg-type]
        ingredients_text="Aqua, Sugar",
    )
    assert without_cert.verdict == ProductHalalVerdict.LIKELY_HALAL
    assert without_cert.certification_verified is False

    with_cert = await analyzer.analyze(
        None,  # type: ignore[arg-type]
        ingredients_text="Water, Sugar",
        certifications=[
            CertificationEvidenceInput(
                issuer="JAKIM",
                is_recognized_issuer=True,
                is_verified=True,
                is_expired=False,
            )
        ],
    )
    assert with_cert.verdict == ProductHalalVerdict.HALAL_VERIFIED
    assert with_cert.certification_verified is True


@pytest.mark.asyncio
async def test_haram_and_e_number_detection() -> None:
    kb = _StubKnowledgeBase(
        [
            _record("Water", IngredientHalalStatus.HALAL),
            _record("Carmine", IngredientHalalStatus.HARAM, e_number="E120"),
        ]
    )
    analyzer = HalalIngredientAnalyzer(knowledge_base=kb)
    res = await analyzer.analyze(None, ingredients_text="Water, E 120")  # type: ignore[arg-type]
    assert res.verdict == ProductHalalVerdict.HARAM_DETECTED
    assert res.classifications[1].status == IngredientHalalStatus.HARAM
    assert res.classifications[1].match_type == "e_number"


@pytest.mark.asyncio
async def test_source_dependent_and_qualifiers() -> None:
    kb = _StubKnowledgeBase(
        [
            _record(
                "Mono- and diglycerides of fatty acids",
                IngredientHalalStatus.CONDITIONAL,
                e_number="E471",
                ins_number="INS471",
                source_requirements="Must be plant-derived rather than porcine.",
            )
        ]
    )
    analyzer = HalalIngredientAnalyzer(knowledge_base=kb)

    unresolved = await analyzer.analyze(None, ingredients_text="INS 471")  # type: ignore[arg-type]
    assert unresolved.verdict == ProductHalalVerdict.DOUBTFUL
    assert unresolved.classifications[0].status == IngredientHalalStatus.CONDITIONAL

    porcine = await analyzer.analyze(None, ingredients_text="E471 (porcine)")  # type: ignore[arg-type]
    assert porcine.verdict == ProductHalalVerdict.HARAM_DETECTED
    assert porcine.classifications[0].status == IngredientHalalStatus.HARAM

    plant = await analyzer.analyze(None, ingredients_text="E471 (vegetable)")  # type: ignore[arg-type]
    assert plant.verdict == ProductHalalVerdict.LIKELY_HALAL
    assert plant.classifications[0].status == IngredientHalalStatus.HALAL


@pytest.mark.asyncio
async def test_unknown_ingredient_never_produces_halal_verdict() -> None:
    kb = _StubKnowledgeBase([_record("Water", IngredientHalalStatus.HALAL)])
    analyzer = HalalIngredientAnalyzer(knowledge_base=kb)

    res = await analyzer.analyze(
        None,  # type: ignore[arg-type]
        ingredients_text="Water, Mystery Additive XYZ",
    )
    assert res.verdict == ProductHalalVerdict.INSUFFICIENT_INFORMATION
    assert res.classifications[1].status == IngredientHalalStatus.UNKNOWN
    assert res.manual_review_required is True


@pytest.mark.asyncio
async def test_empty_ingredients_and_conflicting_evidence() -> None:
    kb = _StubKnowledgeBase(
        [
            _record(
                "Gelatin",
                IngredientHalalStatus.CONDITIONAL,
                review_status=EvidenceReviewStatus.DISPUTED,
            )
        ]
    )
    analyzer = HalalIngredientAnalyzer(knowledge_base=kb)

    empty_res = await analyzer.analyze(None, ingredients_text="   ")  # type: ignore[arg-type]
    assert empty_res.verdict == ProductHalalVerdict.INSUFFICIENT_INFORMATION

    disputed_res = await analyzer.analyze(None, ingredients_text="Gelatin")  # type: ignore[arg-type]
    assert disputed_res.verdict == ProductHalalVerdict.DOUBTFUL
    assert any("Conflicting or disputed" in w for w in disputed_res.warnings)


@pytest.mark.asyncio
async def test_ai_provider_failure_falls_back_to_deterministic_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.halal_engine.ai_assistant.get_settings",
        lambda: SimpleNamespace(openai_api_key="fake-key"),
    )

    async def failing_post(*args, **kwargs):
        raise httpx.ReadTimeout("timeout")

    ai = OptionalAIAssistant()
    monkeypatch.setattr(ai, "_post_json", failing_post)

    kb = _StubKnowledgeBase([_record("Water", IngredientHalalStatus.HALAL)])
    analyzer = HalalIngredientAnalyzer(knowledge_base=kb, ai_assistant=ai)
    res = await analyzer.analyze(
        None,  # type: ignore[arg-type]
        ocr_text="Water",
        use_ai_assistance=True,
    )
    assert res.verdict == ProductHalalVerdict.LIKELY_HALAL
    assert res.ai_assisted is False


@pytest.mark.asyncio
async def test_analyze_product_endpoint_and_db_failure_handling(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await client.post(
        "/api/v1/halal/analyze-product",
        json={
            "barcode": "5449000000996",
            "product_name": "Sample Drink",
            "ingredients_text": "Water, Unknown Syrup",
            "product_source": "open_food_facts",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "insufficient_information"
    assert len(body["ingredient_classifications"]) == 2
    assert body["ingredient_classifications"][0]["status"] == "halal"
    assert body["ingredient_classifications"][1]["status"] == "unknown"

    monkeypatch.setattr(
        "app.api.routes.halal.halal_analyzer",
        HalalIngredientAnalyzer(knowledge_base=_FailingKnowledgeBase()),
    )
    fail_resp = await client.post(
        "/api/v1/halal/analyze-product",
        json={"ingredients_text": "Water"},
    )
    assert fail_resp.status_code == 503
