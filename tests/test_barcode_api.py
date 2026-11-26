import asyncio

import pytest
from httpx import AsyncClient

from app.services.open_food_facts import OpenFoodFactsProduct

VALID_BARCODE = "5449000000996"


def _off_product(
    barcode: str = VALID_BARCODE,
    *,
    name: str | None = "Orange Soap",
    ingredients: list[str] | None = None,
    ingredients_text: str | None = None,
) -> OpenFoodFactsProduct:
    return OpenFoodFactsProduct(
        barcode=barcode,
        name=name,
        brand="Example",
        image_url="https://images.openfoodfacts.org/example.jpg",
        ingredients=ingredients or [],
        ingredients_text=ingredients_text,
        categories=["en:soaps"],
        origins="France",
        labels=["en:organic"],
        nutriments={"energy-kcal_100g": 12.0},
    )


@pytest.mark.asyncio
async def test_barcode_uses_database_product_and_existing_analysis(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await client.post(
        "/api/v1/products/",
        json={"barcode": VALID_BARCODE, "name": "Local soap", "brand": "Local"},
    )
    assert created.status_code == 201

    async def unexpected_external_lookup(barcode: str) -> None:
        raise AssertionError("Database hit should not call Open Food Facts")

    monkeypatch.setattr(
        "app.services.barcode_lookup.fetch_open_food_facts_product",
        unexpected_external_lookup,
    )

    response = await client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_BARCODE}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["product"]["name"] == "Local soap"
    assert data["product"]["source"] is None
    assert data["analysis"]["overall_status"] == "UNKNOWN"
    assert "No ingredients were available to analyze" in data["analysis"]["explanation"]
    assert data["certifications"] == []


@pytest.mark.asyncio
async def test_barcode_imports_off_product_then_uses_database_cache(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups: list[str] = []

    async def external_lookup(barcode: str) -> OpenFoodFactsProduct:
        lookups.append(barcode)
        return _off_product(
            ingredients=["Aqua", "Glycerin", "Limonene"],
            ingredients_text="Aqua, Glycerin, Limonene",
        )

    monkeypatch.setattr(
        "app.services.barcode_lookup.fetch_open_food_facts_product", external_lookup
    )

    first = await client.post("/api/v1/scan/barcode", json={"barcode": VALID_BARCODE})
    second = await client.post("/api/v1/scan/barcode", json={"barcode": VALID_BARCODE})

    assert first.status_code == second.status_code == 200
    assert len(lookups) == 1
    result = first.json()
    assert result["product"]["source"] == "open_food_facts"
    assert result["product"]["ingredients"] == ["Aqua", "Glycerin", "Limonene"]
    assert result["product"]["categories"] == ["en:soaps"]
    assert result["product"]["labels"] == ["en:organic"]
    assert result["product"]["origin_description"] == "France"
    assert result["product"]["nutriments"] == {"energy-kcal_100g": 12.0}
    assert result["analysis"]["overall_status"] == "DOUBTFUL"


@pytest.mark.asyncio
async def test_authenticated_barcode_import_keeps_user_id_after_product_import(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Amina User",
            "email": "amina@example.com",
            "password": "a-secure-password-123",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "amina@example.com", "password": "a-secure-password-123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    async def external_lookup(barcode: str) -> OpenFoodFactsProduct:
        return _off_product(barcode)

    monkeypatch.setattr(
        "app.services.barcode_lookup.fetch_open_food_facts_product", external_lookup
    )

    response = await client.post(
        "/api/v1/scan/barcode",
        json={"barcode": VALID_BARCODE},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["product"]["barcode"] == VALID_BARCODE


@pytest.mark.asyncio
async def test_barcode_with_no_ingredient_data_stays_unknown(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def external_lookup(barcode: str) -> OpenFoodFactsProduct:
        return _off_product(ingredients=[], ingredients_text=None)

    monkeypatch.setattr(
        "app.services.barcode_lookup.fetch_open_food_facts_product", external_lookup
    )

    response = await client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_BARCODE}
    )

    assert response.status_code == 200
    assert response.json()["product"]["ingredients"] == []
    assert response.json()["analysis"]["overall_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_unverified_potentially_sensitive_ingredient_is_not_classified(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def external_lookup(barcode: str) -> OpenFoodFactsProduct:
        return _off_product(
            ingredients=["Gelatin"],
            ingredients_text="Gelatin",
        )

    monkeypatch.setattr(
        "app.services.barcode_lookup.fetch_open_food_facts_product", external_lookup
    )

    response = await client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_BARCODE}
    )

    assert response.status_code == 200
    assert response.json()["product"]["ingredients"] == ["Gelatin"]
    assert response.json()["analysis"]["overall_status"] == "DOUBTFUL"


@pytest.mark.asyncio
async def test_barcode_not_found_returns_controlled_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def external_lookup(barcode: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.barcode_lookup.fetch_open_food_facts_product", external_lookup
    )

    response = await client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_BARCODE}
    )

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "status": "failed",
        "error_code": "PRODUCT_NOT_FOUND",
        "message": "No product was found for this barcode.",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_barcode_external_service_failure_is_sanitized(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.open_food_facts import OpenFoodFactsUnavailable

    async def external_lookup(barcode: str) -> None:
        raise OpenFoodFactsUnavailable("internal timeout detail")

    monkeypatch.setattr(
        "app.services.barcode_lookup.fetch_open_food_facts_product", external_lookup
    )

    response = await client.post(
        "/api/v1/scan/barcode", json={"barcode": VALID_BARCODE}
    )

    assert response.status_code == 503
    assert response.json()["error_code"] == "PRODUCT_LOOKUP_UNAVAILABLE"
    assert "internal" not in response.text


@pytest.mark.asyncio
async def test_invalid_barcode_is_rejected_with_consistent_error(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/scan/barcode", json={"barcode": "5449000000995"}
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_BARCODE"
    assert response.json()["retryable"] is False


@pytest.mark.asyncio
async def test_concurrent_same_barcode_import_is_deduplicated(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups = 0

    async def external_lookup(barcode: str) -> OpenFoodFactsProduct:
        nonlocal lookups
        lookups += 1
        await asyncio.sleep(0.01)
        return _off_product(ingredients=["Water"])

    monkeypatch.setattr(
        "app.services.barcode_lookup.fetch_open_food_facts_product", external_lookup
    )

    first, second = await asyncio.gather(
        client.post("/api/v1/scan/barcode", json={"barcode": VALID_BARCODE}),
        client.post("/api/v1/scan/barcode", json={"barcode": VALID_BARCODE}),
    )

    assert first.status_code == second.status_code == 200
    assert lookups == 1
    assert first.json()["product"]["id"] == second.json()["product"]["id"]
