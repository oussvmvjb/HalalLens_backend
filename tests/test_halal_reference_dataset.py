import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import EvidenceReviewStatus, Ingredient, IngredientHalalStatus, ProductHalalVerdict
from app.services.halal_engine import (
    DatabaseKnowledgeBase,
    HalalIngredientAnalyzer,
    split_and_normalize_ingredients,
)
from scripts.seed_halal_reference_dataset import run_import


@pytest.mark.asyncio
async def test_seed_script_imports_and_is_idempotent() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        first = await run_import(session)
        assert first["imported"] >= 70
        assert first["skipped"] >= 0

        second = await run_import(session)
        assert second["imported"] == 0
        assert second["skipped"] == first["total_defined"]

        total_rows = await session.scalar(select(func.count()).select_from(Ingredient))
        assert total_rows == first["imported"]

        gelatin = await session.scalar(select(Ingredient).where(Ingredient.normalized_name == "gelatin"))
        assert gelatin is not None
        assert gelatin.halal_status == IngredientHalalStatus.CONDITIONAL
        assert gelatin.source_classification == "haram_avoid"

        beer = await session.scalar(select(Ingredient).where(Ingredient.normalized_name == "beer"))
        assert beer is not None
        assert beer.halal_status == IngredientHalalStatus.HARAM

        e471 = await session.scalar(select(Ingredient).where(Ingredient.e_number == "E471"))
        assert e471 is not None
        assert e471.halal_status in {IngredientHalalStatus.CONDITIONAL, IngredientHalalStatus.DOUBTFUL}

    await engine.dispose()


def test_parser_preserves_french_percentages_and_nested_parentheses() -> None:
    text = (
        "Farine complète de blé, Eau, Farine de blé (2,2%), "
        "Huile de colza (origine UE (France, Allemagne)), Jus d'acérola en poudre"
    )
    items = split_and_normalize_ingredients(ingredients_text=text)
    originals = [item.original_text for item in items]
    normalized = [item.normalized_name for item in items]

    assert len(items) == 5
    assert "Farine de blé (2,2%)" in originals
    assert "2%)" not in originals
    assert ")" not in normalized
    assert normalized == [
        "farine complete de ble",
        "eau",
        "farine de ble",
        "huile de colza",
        "jus d'acerola en poudre",
    ]


@pytest.mark.asyncio
async def test_e_number_normalization_variants_match_seeded_record() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        await run_import(session)
        analyzer = HalalIngredientAnalyzer(knowledge_base=DatabaseKnowledgeBase())
        for variant in ("E471", "E-471", "E 471"):
            res = await analyzer.analyze(session, ingredients_text=variant)
            assert len(res.classifications) == 1
            assert res.classifications[0].record is not None
            assert res.classifications[0].record.e_number == "E471"
            assert res.classifications[0].review_status == EvidenceReviewStatus.PENDING_REVIEW
            assert res.verdict == ProductHalalVerdict.DOUBTFUL

    await engine.dispose()


@pytest.mark.asyncio
async def test_french_product_analysis_with_seeded_db_and_empty_db() -> None:
    french_ingredients = (
        "Farine complète de blé, Eau, Farine de blé, Huile de colza, Levure, "
        "Arôme naturel, Sel, Maïs, Gluten de blé, Acérola, Farine de blé (2,2%), "
        "Farine de maïs fermentée, Jus d'acérola en poudre"
    )

    # 1. Empty database: controlled fallback handles plant/mineral staples and flags Arôme naturel
    empty_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with empty_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    empty_sessions = async_sessionmaker(empty_engine, expire_on_commit=False, class_=AsyncSession)
    async with empty_sessions() as session:
        analyzer = HalalIngredientAnalyzer(knowledge_base=DatabaseKnowledgeBase())
        empty_res = await analyzer.analyze(session, ingredients_text=french_ingredients)
        assert empty_res.verdict == ProductHalalVerdict.DOUBTFUL
        assert empty_res.certification_verified is False
        by_orig = {c.normalized.original_text: c for c in empty_res.classifications}
        assert by_orig["Farine complète de blé"].status == IngredientHalalStatus.HALAL
        assert by_orig["Eau"].status == IngredientHalalStatus.HALAL
        assert by_orig["Huile de colza"].status == IngredientHalalStatus.HALAL
        assert by_orig["Levure"].status == IngredientHalalStatus.HALAL
        assert by_orig["Sel"].status == IngredientHalalStatus.HALAL
        assert by_orig["Maïs"].status == IngredientHalalStatus.HALAL
        assert by_orig["Gluten de blé"].status == IngredientHalalStatus.HALAL
        assert by_orig["Acérola"].status == IngredientHalalStatus.HALAL
        assert by_orig["Farine de maïs fermentée"].status == IngredientHalalStatus.HALAL
        assert by_orig["Jus d'acérola en poudre"].status == IngredientHalalStatus.HALAL
        assert by_orig["Arôme naturel"].status == IngredientHalalStatus.DOUBTFUL
        assert "Arôme naturel" in empty_res.explanation or "Artificial and natural flavorings" in empty_res.explanation
    await empty_engine.dispose()

    # 2. Seeded database: matches database records with aliases and accent normalization
    seeded_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with seeded_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    seeded_sessions = async_sessionmaker(seeded_engine, expire_on_commit=False, class_=AsyncSession)
    async with seeded_sessions() as session:
        await run_import(session)
        analyzer = HalalIngredientAnalyzer(knowledge_base=DatabaseKnowledgeBase())
        seeded_res = await analyzer.analyze(session, ingredients_text=french_ingredients)
        assert seeded_res.verdict == ProductHalalVerdict.DOUBTFUL
        assert seeded_res.certification_verified is False
        by_orig = {c.normalized.original_text: c for c in seeded_res.classifications}
        assert by_orig["Farine complète de blé"].status == IngredientHalalStatus.HALAL
        assert by_orig["Farine complète de blé"].record is not None
        assert by_orig["Arôme naturel"].status == IngredientHalalStatus.DOUBTFUL
        assert by_orig["Arôme naturel"].record is not None
    await seeded_engine.dispose()


@pytest.mark.asyncio
async def test_scan_history_and_details_endpoints_return_200_with_halal_analysis(
    client: AsyncClient,
) -> None:
    register_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Test User",
            "email": "history_halal@example.com",
            "password": "Password123!",
            "preferred_language": "fr",
        },
    )
    assert register_resp.status_code == 201
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "history_halal@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    product_resp = await client.post(
        "/api/v1/products/",
        json={
            "barcode": "3228857000166",
            "name": "14 Maxi Tranches complet sans sucres ajoutés",
            "brand": "Harrys",
        },
    )
    assert product_resp.status_code == 201

    create_resp = await client.post(
        "/api/v1/scans/barcode",
        headers=headers,
        json={"barcode": "3228857000166"},
    )
    assert create_resp.status_code == 201
    scan_id = create_resp.json()["id"]

    analyze_resp = await client.post(
        "/api/v1/scans/analyze",
        headers=headers,
        json={
            "scan_id": scan_id,
            "original_ingredients_text": "Farine complète de blé, Eau, Farine de blé (2,2%), Arôme naturel",
            "detected_language": "fr",
        },
    )
    assert analyze_resp.status_code == 200

    history_resp = await client.get("/api/v1/scans/history", headers=headers)
    assert history_resp.status_code == 200
    items = history_resp.json()
    assert len(items) == 1
    assert items[0]["analysis"]["verdict"] == "doubtful"
    assert len(items[0]["analysis"]["ingredients_analysis"]) == 4

    details_resp = await client.get(f"/api/v1/scans/{scan_id}", headers=headers)
    assert details_resp.status_code == 200
    assert details_resp.json()["analysis"]["verdict"] == "doubtful"
