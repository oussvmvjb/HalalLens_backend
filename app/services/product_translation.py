import hashlib
import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import ProductDetailsTranslationCache
from app.schemas.product_translation import (
    ProductDetailsText,
    ProductDetailsTranslationRequest,
    ProductDetailsTranslationResponse,
)

logger = logging.getLogger(__name__)
TRANSLATION_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

DEEPL_LANGUAGE_MAP: dict[str, str] = {
    "en": "EN",
    "en-us": "EN-US",
    "en-gb": "EN-GB",
    "zh": "ZH",
    "zh-cn": "ZH",
    "zh-hans": "ZH",
    "zh-tw": "ZH-HANT",
    "zh-hant": "ZH-HANT",
    "fr": "FR",
    "ar": "AR",
    "de": "DE",
}


class ProductTranslationUnavailable(Exception):
    pass


class UnsupportedTranslationLanguage(ProductTranslationUnavailable):
    pass


def map_deepl_language(language_code: str) -> str:
    """Map an application language code to a supported DeepL target language code."""
    normalized = language_code.strip().lower().replace("_", "-")
    mapped = DEEPL_LANGUAGE_MAP.get(normalized)
    if mapped is None:
        raise UnsupportedTranslationLanguage(
            f"Unsupported translation target language: {language_code}"
        )
    return mapped


async def translate_product_details(
    db: AsyncSession,
    request: ProductDetailsTranslationRequest,
) -> ProductDetailsTranslationResponse:
    map_deepl_language(request.target_language)

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

    cached = await _read_cache_entry(db, cache_key)
    if cached is not None:
        return ProductDetailsTranslationResponse(
            target_language=request.target_language,
            details=cached,
        )

    try:
        translated_details = await _translate_details(
            request.details, request.target_language
        )
    except ValidationError as error:
        raise ProductTranslationUnavailable from error

    await _write_cache_entry(db, cache_key, translated_details)

    return ProductDetailsTranslationResponse(
        target_language=request.target_language,
        details=translated_details,
    )


async def _read_cache_entry(
    db: AsyncSession,
    cache_key: tuple[str, str],
) -> ProductDetailsText | None:
    try:
        cached = await db.get(ProductDetailsTranslationCache, cache_key)
    except SQLAlchemyError:
        logger.warning("Failed to read product translation cache; falling back to DeepL")
        return None
    if cached is None:
        return None
    try:
        return ProductDetailsText.model_validate(cached.translated_details)
    except ValidationError:
        logger.warning("Cached product translation entry is invalid; re-translating")
        return None


async def _write_cache_entry(
    db: AsyncSession,
    cache_key: tuple[str, str],
    translated_details: ProductDetailsText,
) -> None:
    source_hash, target_language = cache_key
    cache_entry = ProductDetailsTranslationCache(
        source_hash=source_hash,
        target_language=target_language,
        translated_details=translated_details.model_dump(mode="json"),
    )
    db.add(cache_entry)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.debug("Concurrent translation cache insert handled for %s", source_hash)
    except SQLAlchemyError:
        await db.rollback()
        logger.warning("Failed to persist product translation cache entry")


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
    source_language: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    deepl_target = map_deepl_language(target_language)
    deepl_source = (
        map_deepl_language(source_language).split("-", 1)[0]
        if source_language
        else None
    )

    if not texts:
        return []

    settings = get_settings()
    api_key = (settings.deepl_api_key or "").strip()
    if not api_key:
        logger.warning("DeepL API key is not configured")
        raise ProductTranslationUnavailable

    base_url = (settings.deepl_api_base_url or "https://api-free.deepl.com").strip().rstrip("/")
    translate_url = f"{base_url}/v2/translate"

    if client is not None:
        return await _request_translations(
            client, translate_url, api_key, texts, deepl_target, deepl_source
        )

    async with httpx.AsyncClient(timeout=TRANSLATION_TIMEOUT) as http_client:
        return await _request_translations(
            http_client, translate_url, api_key, texts, deepl_target, deepl_source
        )


async def _request_translations(
    client: httpx.AsyncClient,
    translate_url: str,
    api_key: str,
    texts: list[str],
    target_lang: str,
    source_lang: str | None = None,
) -> list[str]:
    payload_body: dict[str, Any] = {
        "text": texts,
        "target_lang": target_lang,
    }
    if source_lang is not None:
        payload_body["source_lang"] = source_lang

    try:
        response = await client.post(
            translate_url,
            headers={
                "Authorization": f"DeepL-Auth-Key {api_key}",
                "Content-Type": "application/json",
            },
            json=payload_body,
        )
    except httpx.TimeoutException as error:
        logger.warning("DeepL translation request timed out")
        raise ProductTranslationUnavailable from error
    except httpx.RequestError as error:
        logger.warning("DeepL translation network request failed")
        raise ProductTranslationUnavailable from error

    if response.status_code in {401, 403}:
        logger.warning("DeepL translation authentication failed (HTTP %s)", response.status_code)
        raise ProductTranslationUnavailable
    if response.status_code == 429:
        logger.warning("DeepL translation rate limit exceeded (HTTP 429)")
        raise ProductTranslationUnavailable
    if response.status_code == 456:
        logger.warning("DeepL translation quota exceeded (HTTP 456)")
        raise ProductTranslationUnavailable
    if response.status_code >= 400:
        logger.warning("DeepL translation returned HTTP %s", response.status_code)
        raise ProductTranslationUnavailable

    try:
        payload: Any = response.json()
    except ValueError as error:
        logger.warning("DeepL translation returned invalid JSON")
        raise ProductTranslationUnavailable from error

    translations = payload.get("translations") if isinstance(payload, dict) else None
    if not isinstance(translations, list) or len(translations) != len(texts):
        logger.warning("DeepL translation response missing expected translations list")
        raise ProductTranslationUnavailable

    results: list[str] = []
    for original, item in zip(texts, translations, strict=True):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ProductTranslationUnavailable
        detected_language = item.get("detected_source_language")
        translated = item["text"]
        if (
            isinstance(detected_language, str)
            and _same_language(detected_language, target_lang)
        ):
            results.append(original)
        else:
            results.append(translated)
    return results


def _same_language(source: str, target: str) -> bool:
    source_code = source.lower().replace("_", "-")
    target_code = target.lower().replace("_", "-")
    if target_code == "zh":
        return source_code in {"zh", "zh-hans", "zh-cn"}
    return source_code == target_code or source_code.split("-", 1)[0] == target_code.split("-", 1)[0]


__all__ = [
    "DEEPL_LANGUAGE_MAP",
    "ProductTranslationUnavailable",
    "UnsupportedTranslationLanguage",
    "map_deepl_language",
    "translate_product_details",
    "translate_texts",
]
