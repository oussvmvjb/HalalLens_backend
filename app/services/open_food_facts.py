import math
import re
from dataclasses import dataclass
from typing import Any

import httpx

OFF_API_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
OFF_TIMEOUT_SECONDS = 8.0
OFF_USER_AGENT = "HalalLens/1.0 (product lookup)"
_NUTRIMENT_KEYS = (
    "energy-kcal_100g",
    "fat_100g",
    "saturated-fat_100g",
    "carbohydrates_100g",
    "sugars_100g",
    "fiber_100g",
    "proteins_100g",
    "salt_100g",
    "sodium_100g",
)
_OFF_FIELDS = (
    "code,status,product_name,product_name_en,brands,image_front_url,image_url,"
    "ingredients,ingredients_text,categories,categories_tags,origins,origins_tags,"
    "labels,labels_tags,nutriments"
)


class OpenFoodFactsUnavailable(Exception):
    pass


class OpenFoodFactsInvalidResponse(Exception):
    pass


@dataclass(frozen=True)
class OpenFoodFactsProduct:
    barcode: str
    name: str | None
    brand: str | None
    image_url: str | None
    ingredients: list[str]
    ingredients_text: str | None
    categories: list[str]
    origins: str | None
    labels: list[str]
    nutriments: dict[str, float]

    def as_external_data(self) -> dict[str, Any]:
        return {
            "product_name": self.name,
            "ingredients": self.ingredients,
            "ingredients_text": self.ingredients_text,
            "categories": self.categories,
            "origin_description": self.origins,
            "labels": self.labels,
            "nutriments": self.nutriments,
            "source_url": f"https://world.openfoodfacts.org/product/{self.barcode}",
        }


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = re.split(r"\s*,\s*", value.strip())
    elif isinstance(value, list):
        values = value
    else:
        return []

    result: list[str] = []
    for item in values:
        text = item if isinstance(item, str) else None
        if isinstance(item, dict):
            text = item.get("text") if isinstance(item.get("text"), str) else None
        normalized = _clean_text(text)
        if normalized:
            result.append(normalized)
    return result


def _ingredients(product: dict[str, Any], ingredients_text: str | None) -> list[str]:
    items = product.get("ingredients")
    if isinstance(items, list):
        values = _string_list(items)
        if values:
            return values
    return _string_list(ingredients_text)


def _nutriments(product: dict[str, Any]) -> dict[str, float]:
    raw = product.get("nutriments")
    if not isinstance(raw, dict):
        return {}

    result: dict[str, float] = {}
    for key in _NUTRIMENT_KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result[key] = number
    return result


def _image_url(product: dict[str, Any]) -> str | None:
    for key in ("image_front_url", "image_url"):
        value = _clean_text(product.get(key))
        if value and value.startswith("https://") and len(value) <= 2048:
            return value
    return None


def normalize_open_food_facts_product(
    barcode: str, payload: Any
) -> OpenFoodFactsProduct | None:
    if not isinstance(payload, dict):
        raise OpenFoodFactsInvalidResponse("Response body is not an object")

    status = payload.get("status")
    if status == 0:
        return None
    product = payload.get("product")
    if not isinstance(product, dict):
        raise OpenFoodFactsInvalidResponse("Response is missing product data")

    returned_barcode = _clean_text(product.get("code")) or _clean_text(payload.get("code"))
    if returned_barcode and returned_barcode != barcode:
        raise OpenFoodFactsInvalidResponse("Response barcode does not match request")

    ingredients_text = _clean_text(product.get("ingredients_text"))
    origins = _clean_text(product.get("origins")) or _clean_text(
        product.get("origins_tags")
    )
    return OpenFoodFactsProduct(
        barcode=barcode,
        name=_clean_text(product.get("product_name"))
        or _clean_text(product.get("product_name_en")),
        brand=_clean_text(product.get("brands")),
        image_url=_image_url(product),
        ingredients=_ingredients(product, ingredients_text),
        ingredients_text=ingredients_text,
        categories=_string_list(product.get("categories_tags"))
        or _string_list(product.get("categories")),
        origins=origins,
        labels=_string_list(product.get("labels_tags"))
        or _string_list(product.get("labels")),
        nutriments=_nutriments(product),
    )


async def fetch_open_food_facts_product(
    barcode: str,
    client: httpx.AsyncClient | None = None,
) -> OpenFoodFactsProduct | None:
    if client is not None:
        return await _fetch_with_client(barcode, client)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(OFF_TIMEOUT_SECONDS),
            headers={"User-Agent": OFF_USER_AGENT},
        ) as http_client:
            return await _fetch_with_client(barcode, http_client)
    except (httpx.TimeoutException, httpx.RequestError) as error:
        raise OpenFoodFactsUnavailable from error


async def _fetch_with_client(
    barcode: str, client: httpx.AsyncClient
) -> OpenFoodFactsProduct | None:
    try:
        response = await client.get(
            OFF_API_URL.format(barcode=barcode),
            params={"fields": _OFF_FIELDS},
        )
        response.raise_for_status()
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as error:
        raise OpenFoodFactsUnavailable from error
    try:
        payload = response.json()
    except ValueError as error:
        raise OpenFoodFactsInvalidResponse from error
    return normalize_open_food_facts_product(barcode, payload)
