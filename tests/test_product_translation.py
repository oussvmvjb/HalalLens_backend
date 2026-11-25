import json
from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient, MockTransport, Response
from sqlalchemy.exc import IntegrityError

from app.schemas.product_translation import (
    ProductDetailsText,
    ProductDetailsTranslationRequest,
)
from app.services.product_translation import (
    ProductTranslationUnavailable,
    UnsupportedTranslationLanguage,
    map_deepl_language,
    translate_product_details,
    translate_texts,
)


def _deepl_settings(
    auth_key: str | None = "test-deepl-key",
    base_url: str = "https://api-free.deepl.com",
) -> SimpleNamespace:
    return SimpleNamespace(deepl_api_key=auth_key, deepl_api_base_url=base_url)


@pytest.mark.asyncio
async def test_deepl_translation_preserves_text_already_in_target_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: _deepl_settings(),
    )

    async def handler(request: httpx.Request) -> Response:
        assert request.url.path == "/v2/translate"
        assert "key" not in request.url.params
        assert request.headers["Authorization"] == "DeepL-Auth-Key test-deepl-key"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {"text": ["洗面奶", "Original English"], "target_lang": "EN"}
        return Response(
            200,
            json={
                "translations": [
                    {"text": "Facial Cleanser", "detected_source_language": "ZH"},
                    {"text": "unexpected change", "detected_source_language": "EN"},
                ]
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        translated = await translate_texts(
            ["洗面奶", "Original English"], "en", client=client
        )

    assert translated == ["Facial Cleanser", "Original English"]


@pytest.mark.asyncio
async def test_deepl_translation_maps_chinese_variants_and_preserves_simplified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: _deepl_settings(base_url="https://api.deepl.com/"),
    )

    async def handler(request: httpx.Request) -> Response:
        assert str(request.url) == "https://api.deepl.com/v2/translate"
        body = json.loads(request.content.decode("utf-8"))
        assert body["target_lang"] == "ZH"
        return Response(
            200,
            json={
                "translations": [
                    {"text": "产品", "detected_source_language": "EN"},
                    {"text": "unexpected change", "detected_source_language": "ZH"},
                ]
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        translated = await translate_texts(["Product", "配料"], "zh-CN", client=client)

    assert translated == ["产品", "配料"]
    assert map_deepl_language("zh-TW") == "ZH-HANT"
    assert map_deepl_language("fr") == "FR"
    assert map_deepl_language("ar") == "AR"
    assert map_deepl_language("de") == "DE"


@pytest.mark.asyncio
async def test_deepl_skips_empty_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: _deepl_settings(),
    )
    assert await translate_texts([], "en") == []


@pytest.mark.asyncio
async def test_product_details_translation_is_cached_and_preserves_order(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], str]] = []

    async def fake_translate(texts: list[str], target_language: str) -> list[str]:
        calls.append((texts, target_language))
        return [f"{target_language}: {text}" for text in texts]

    monkeypatch.setattr(
        "app.services.product_translation.translate_texts", fake_translate
    )
    request = {
        "target_language": "en",
        "details": {
            "product_name": "洁面乳",
            "brand": "清泉",
            "ingredients": ["水", "   ", "甘油"],
            "categories": ["个人护理"],
            "origin_description": "中国",
            "labels": ["无香料"],
            "certification_issuers": ["清真认证机构"],
            "analysis_explanation": "尚未完成验证。",
        },
    }

    first = await client.post("/api/v1/translations/product-details", json=request)
    second = await client.post("/api/v1/translations/product-details", json=request)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["details"]["ingredients"] == ["en: 水", "   ", "en: 甘油"]
    assert first.json()["details"]["product_name"] == "en: 洁面乳"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_product_translation_without_api_key_returns_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: _deepl_settings(auth_key=None),
    )
    response = await client.post(
        "/api/v1/translations/product-details",
        json={
            "target_language": "fr",
            "details": {"analysis_explanation": "No analysis yet."},
        },
    )

    assert response.status_code == 503
    assert response.json()["error_code"] == "TRANSLATION_UNAVAILABLE"
    assert response.json()["retryable"] is True


@pytest.mark.parametrize("status_code", [401, 403, 429, 456, 500, 503])
@pytest.mark.asyncio
async def test_deepl_http_errors_raise_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: _deepl_settings(),
    )

    async def handler(request: httpx.Request) -> Response:
        return Response(status_code, json={"message": "error"})

    async with AsyncClient(transport=MockTransport(handler)) as client:
        with pytest.raises(ProductTranslationUnavailable):
            await translate_texts(["Hello"], "fr", client=client)


@pytest.mark.asyncio
async def test_deepl_timeout_and_malformed_json_raise_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: _deepl_settings(),
    )

    async def timeout_handler(request: httpx.Request) -> Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with AsyncClient(transport=MockTransport(timeout_handler)) as client:
        with pytest.raises(ProductTranslationUnavailable):
            await translate_texts(["Hello"], "fr", client=client)

    async def bad_json_handler(request: httpx.Request) -> Response:
        return Response(200, text="not-json")

    async with AsyncClient(transport=MockTransport(bad_json_handler)) as client:
        with pytest.raises(ProductTranslationUnavailable):
            await translate_texts(["Hello"], "fr", client=client)

    async def bad_shape_handler(request: httpx.Request) -> Response:
        return Response(200, json={"translations": [{"wrong_key": "Bonjour"}]})

    async with AsyncClient(transport=MockTransport(bad_shape_handler)) as client:
        with pytest.raises(ProductTranslationUnavailable):
            await translate_texts(["Hello"], "fr", client=client)


@pytest.mark.asyncio
async def test_unsupported_language_raises_clear_error() -> None:
    with pytest.raises(UnsupportedTranslationLanguage):
        await translate_texts(["Hello"], "xx-INVALID")


@pytest.mark.asyncio
async def test_concurrent_cache_insertion_is_handled_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_translate(texts: list[str], target_language: str) -> list[str]:
        return [f"{target_language}: {t}" for t in texts]

    monkeypatch.setattr(
        "app.services.product_translation.translate_texts", fake_translate
    )

    rolled_back = False

    class _FakeDB:
        async def get(self, model, key):
            return None

        def add(self, obj):
            return None

        async def commit(self):
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        async def rollback(self):
            nonlocal rolled_back
            rolled_back = True

    req = ProductDetailsTranslationRequest(
        target_language="fr",
        details=ProductDetailsText(analysis_explanation="Hello"),
    )
    res = await translate_product_details(_FakeDB(), req)  # type: ignore[arg-type]
    assert rolled_back is True
    assert res.details.analysis_explanation == "fr: Hello"
