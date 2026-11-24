from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient, MockTransport, Response

from app.services.product_translation import (
    translate_texts,
)


@pytest.mark.asyncio
async def test_google_translation_preserves_text_already_in_target_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: SimpleNamespace(google_translation_api_key="test-key"),
    )

    async def handler(request: httpx.Request) -> Response:
        assert request.url.params["key"] == "test-key"
        return Response(
            200,
            json={
                "data": {
                    "translations": [
                        {
                            "translatedText": "Soap",
                            "detectedSourceLanguage": "zh-CN",
                        },
                        {
                            "translatedText": "unexpected change",
                            "detectedSourceLanguage": "en",
                        },
                    ]
                }
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        translated = await translate_texts(
            ["洗面奶", "Original English"], "en", client=client
        )

    assert translated == ["Soap", "Original English"]


@pytest.mark.asyncio
async def test_google_translation_converts_traditional_chinese_to_simplified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: SimpleNamespace(google_translation_api_key="test-key"),
    )

    async def handler(request: httpx.Request) -> Response:
        return Response(
            200,
            json={
                "data": {
                    "translations": [
                        {
                            "translatedText": "产品",
                            "detectedSourceLanguage": "zh-TW",
                        },
                        {
                            "translatedText": "unexpected change",
                            "detectedSourceLanguage": "zh-CN",
                        },
                    ]
                }
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        translated = await translate_texts(
            ["產品", "配料"], "zh-CN", client=client
        )

    assert translated == ["产品", "配料"]


@pytest.mark.asyncio
async def test_product_details_translation_is_cached_and_preserves_input(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], str]] = []

    async def translate(texts: list[str], target_language: str) -> list[str]:
        calls.append((texts, target_language))
        return [f"{target_language}: {text}" for text in texts]

    monkeypatch.setattr(
        "app.services.product_translation.translate_texts", translate
    )
    request = {
        "target_language": "en",
        "details": {
            "product_name": "洁面乳",
            "brand": "清泉",
            "ingredients": ["水", "甘油"],
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
    assert first.json()["details"]["ingredients"] == [
        "en: 水",
        "en: 甘油",
    ]
    assert first.json()["details"]["product_name"] == "en: 洁面乳"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_product_translation_without_api_key_returns_retryable_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.product_translation.get_settings",
        lambda: SimpleNamespace(google_translation_api_key=None),
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
