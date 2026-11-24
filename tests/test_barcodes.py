import pytest
from httpx import AsyncClient, MockTransport, Response

from app.services.barcodes import is_valid_gtin
from app.services.open_food_facts import (
    OpenFoodFactsInvalidResponse,
    fetch_open_food_facts_product,
)


@pytest.mark.parametrize(
    "barcode",
    ["5449000000996", "036000291452", "96385074", "04252614"],
)
def test_valid_gtin_ean_upc_and_upc_e_barcodes(barcode: str) -> None:
    assert is_valid_gtin(barcode)


@pytest.mark.parametrize(
    "barcode",
    ["", "1234567", "5449000000995", "０３６０００２９１４５２", "14252614"],
)
def test_invalid_gtin_barcode_or_check_digit(barcode: str) -> None:
    assert not is_valid_gtin(barcode)


@pytest.mark.asyncio
async def test_open_food_facts_normalizes_source_fields_without_inventing_data() -> None:
    async def handler(request):
        return Response(
            200,
            json={
                "status": 1,
                "product": {
                    "code": "5449000000996",
                    "product_name": "Soap",
                    "brands": "Brand",
                    "ingredients": [{"text": "Aqua"}, {"text": "Limonene"}],
                    "ingredients_text": "Aqua, Limonene",
                    "categories_tags": ["en:soaps"],
                    "labels": "organic, vegan",
                    "origins": "France",
                    "nutriments": {"energy-kcal_100g": "10", "fat_100g": "unknown"},
                },
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        product = await fetch_open_food_facts_product("5449000000996", client)

    assert product is not None
    assert product.name == "Soap"
    assert product.ingredients == ["Aqua", "Limonene"]
    assert product.labels == ["organic", "vegan"]
    assert product.nutriments == {"energy-kcal_100g": 10.0}
    assert product.image_url is None


@pytest.mark.asyncio
async def test_open_food_facts_rejects_mismatched_response_barcode() -> None:
    async def handler(request):
        return Response(
            200,
            json={
                "status": 1,
                "product": {"code": "0000000000000", "product_name": "Wrong"},
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        with pytest.raises(OpenFoodFactsInvalidResponse):
            await fetch_open_food_facts_product("5449000000996", client)
