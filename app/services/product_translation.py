import hashlib
import html
import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import ProductDetailsTranslationCache
from app.schemas.product_translation import (
    ProductDetailsText,
    ProductDetailsTranslationRequest,
    ProductDetailsTranslationResponse,
)

logger = logging.getLogger(__name__)
GOOGLE_TRANSLATION_URL = "https://translation.googleapis.com/language/translate/v2"
TRANSLATION_TIMEOUT_SECONDS = 15.0


class ProductTranslationUnavailable(Exception):
    pass


async def translate_product_details(
    db: AsyncSession,
    request: ProductDetailsTranslationRequest,
) -> ProductDetailsTranslationResponse:
    source_details = request.details.model_dump(mode="json")
    source_hash = hashlib.sha256(
        json.dumps(
            source_details,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cache_key = (source_hash, request.target_language)

    cached = await db.get(ProductDetailsTranslationCache, cache_key)
    if cached is not None:
        return ProductDetailsTranslationResponse(
            target_language=request.target_language,
            details=ProductDetailsText.model_validate(cached.translated_details),
        )

    try:
        translated_details = await _translate_details(
            request.details, request.target_language
        )
    except ValidationError as error:
        raise ProductTranslationUnavailable from error
    cache_entry = ProductDetailsTranslationCache(
        source_hash=source_hash,
        target_language=request.target_language,
        translated_details=translated_details.model_dump(mode="json"),
    )
    db.add(cache_entry)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        cached = await db.get(ProductDetailsTranslationCache, cache_key)
        if cached is None:
            raise
        translated_details = ProductDetailsText.model_validate(
            cached.translated_details
        )

    return ProductDetailsTranslationResponse(
        target_language=request.target_language,
        details=translated_details,
    )


async def _translate_details(
    details: ProductDetailsText,
    target_language: str,
) -> ProductDetailsText:
    translated_values: list[str] = []
    slots: list[tuple[str, int | None]] = []
    source = details.model_dump(mode="python")

    for field in (
        "product_name",
        "brand",
        "origin_description",
        "analysis_explanation",
    ):
        value = source[field]
        if isinstance(value, str) and value.strip():
            slots.append((field, None))
            translated_values.append(value)

    for field in ("ingredients", "categories", "labels", "certification_issuers"):
        for index, value in enumerate(source[field]):
            if value.strip():
                slots.append((field, index))
                translated_values.append(value)

    if translated_values:
        results = await translate_texts(translated_values, target_language)
        if len(results) != len(slots):
            raise ProductTranslationUnavailable
        for (field, index), translated in zip(slots, results, strict=True):
            if index is None:
                source[field] = translated
            else:
                source[field][index] = translated

    return ProductDetailsText.model_validate(source)


async def translate_texts(
    texts: list[str],
    target_language: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    api_key = get_settings().google_translation_api_key
    if not api_key:
        raise ProductTranslationUnavailable
    if not texts:
        return []

    if client is not None:
        return await _request_translations(client, api_key, texts, target_language)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(TRANSLATION_TIMEOUT_SECONDS)
        ) as http_client:
            return await _request_translations(
                http_client, api_key, texts, target_language
            )
    except (httpx.RequestError, httpx.HTTPStatusError) as error:
        logger.warning("Google Cloud Translation request failed")
        raise ProductTranslationUnavailable from error


async def _request_translations(
    client: httpx.AsyncClient,
    api_key: str,
    texts: list[str],
    target_language: str,
) -> list[str]:
    try:
        response = await client.post(
            GOOGLE_TRANSLATION_URL,
            params={"key": api_key},
            json={"q": texts, "target": target_language, "format": "text"},
        )
        response.raise_for_status()
        payload: Any = response.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as error:
        logger.warning("Google Cloud Translation request failed")
        raise ProductTranslationUnavailable from error

    data = payload.get("data") if isinstance(payload, dict) else None
    translations = data.get("translations") if isinstance(data, dict) else None
    if not isinstance(translations, list) or len(translations) != len(texts):
        raise ProductTranslationUnavailable

    results: list[str] = []
    for original, item in zip(texts, translations, strict=True):
        if not isinstance(item, dict) or not isinstance(item.get("translatedText"), str):
            raise ProductTranslationUnavailable
        detected_language = item.get("detectedSourceLanguage")
        translated = item["translatedText"]
        if (
            isinstance(detected_language, str)
            and _same_language(detected_language, target_language)
        ):
            results.append(original)
        else:
            results.append(html.unescape(translated))
    return results


def _same_language(source: str, target: str) -> bool:
    source_code = source.lower().replace("_", "-")
    target_code = target.lower().replace("_", "-")
    if target_code == "zh-cn":
        return source_code in {"zh", "zh-cn"}
    return source_code == target_code or source_code.split("-", 1)[0] == target_code


__all__ = [
    "ProductTranslationUnavailable",
    "translate_product_details",
    "translate_texts",
]
