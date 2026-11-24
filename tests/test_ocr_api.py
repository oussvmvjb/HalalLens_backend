import asyncio
import io
import threading
import time

import pytest
from httpx import AsyncClient
from PIL import Image

from app.api.routes.ocr import MAX_UPLOAD_BYTES
from app.schemas.ocr import OCRTextRegion
from app.services.ocr_service import OCRInferenceError, OCRInitializationError


def gif_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(output, format="GIF")
    return output.getvalue()


def png_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_text_extraction_rejects_unsupported_image_format(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/scan/extract-text",
        files={"image": ("label.gif", gif_image(), "image/gif")},
    )

    assert response.status_code == 415
    assert response.json() == {
        "success": False,
        "status": "failed",
        "error_code": "UNSUPPORTED_IMAGE_FORMAT",
        "message": "Choose a JPG, PNG, or WebP image.",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_text_extraction_rejects_corrupt_image(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/scan/extract-text",
        files={"image": ("label.png", b"\x89PNG\r\n\x1a\nnot-an-image", "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_IMAGE"


@pytest.mark.asyncio
async def test_text_extraction_returns_recognized_regions(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regions = [
        OCRTextRegion(
            text="Ingredients: wheat",
            confidence=0.95,
            bounding_box=[[1, 2], [3, 2], [3, 4], [1, 4]],
        )
    ]
    monkeypatch.setattr("app.api.routes.ocr.ocr_runtime.extract_text", lambda *_: regions)

    response = await client.post(
        "/api/v1/scan/extract-text",
        files={"image": ("label.png", png_image(), "image/png")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data.pop("scan_id")
    assert data == {
        "success": True,
        "status": "completed",
        "message": "Text extracted successfully.",
        "data": {
            "extracted_text": [
                {
                    "text": "Ingredients: wheat",
                    "confidence": 0.95,
                    "bounding_box": [
                        [1.0, 2.0],
                        [3.0, 2.0],
                        [3.0, 4.0],
                        [1.0, 4.0],
                    ],
                }
            ]
        },
        "full_text": "Ingredients: wheat",
        "language": ["en", "fr", "ar"],
        "extracted_text": [
            {
                "text": "Ingredients: wheat",
                "confidence": 0.95,
                "bounding_box": [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]],
            }
        ],
        "ingredients": [
            {
                "original_text": "wheat",
                "normalized_text": None,
                "confidence": 0.95,
                "normalization_confidence": 0.0,
                "needs_review": True,
            }
        ],
        "warnings": [
            "Some ingredient entries did not match the local ingredient dictionary."
        ],
    }


@pytest.mark.asyncio
async def test_text_extraction_accepts_language_configuration(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_languages: list[str] = []

    def fake_extract_text(_: bytes, languages: list[str]) -> list[OCRTextRegion]:
        selected_languages.extend(languages)
        return []

    monkeypatch.setattr("app.api.routes.ocr.ocr_runtime.extract_text", fake_extract_text)

    response = await client.post(
        "/api/v1/scan/extract-text",
        files=[
            ("image", ("label.png", png_image(), "image/png")),
            ("languages", (None, "fr")),
            ("languages", (None, "en")),
            ("languages", (None, "zh_CN")),
        ],
    )

    assert response.status_code == 200
    assert selected_languages == ["fr", "en", "zh_CN"]
    assert response.json()["language"] == ["fr", "en", "zh_CN"]


@pytest.mark.asyncio
async def test_text_extraction_rejects_empty_image(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/scan/extract-text",
        files={"image": ("empty.jpg", b"", "image/jpeg")},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "EMPTY_IMAGE"


@pytest.mark.asyncio
async def test_text_extraction_enforces_upload_limit(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/scan/extract-text",
        files={"image": ("large.jpg", b"x" * (MAX_UPLOAD_BYTES + 1), "image/jpeg")},
    )

    assert response.status_code == 413
    assert response.json()["error_code"] == "IMAGE_TOO_LARGE"


@pytest.mark.asyncio
async def test_text_extraction_returns_consistent_validation_error(
    client: AsyncClient,
) -> None:
    response = await client.post("/api/v1/scan/extract-text")

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "status": "failed",
        "error_code": "INVALID_OCR_REQUEST",
        "message": "The image request is incomplete or invalid.",
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_slow_ocr_completes_before_configured_timeout(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_extract(_: bytes, __: list[str]) -> list[OCRTextRegion]:
        time.sleep(0.08)
        return []

    monkeypatch.setattr("app.api.routes.ocr.ocr_runtime.extract_text", slow_extract)
    monkeypatch.setattr("app.api.routes.ocr.OCR_PROCESSING_TIMEOUT_SECONDS", 1)

    response = await client.post(
        "/api/v1/scan/extract-text",
        files={"image": ("label.png", png_image(), "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_ocr_timeout_returns_retryable_error_and_worker_finishes(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_started = threading.Event()
    release_worker = threading.Event()

    def slow_extract(_: bytes, __: list[str]) -> list[OCRTextRegion]:
        worker_started.set()
        release_worker.wait(timeout=2)
        return []

    monkeypatch.setattr("app.api.routes.ocr.ocr_runtime.extract_text", slow_extract)
    monkeypatch.setattr("app.api.routes.ocr.OCR_PROCESSING_TIMEOUT_SECONDS", 0.05)

    try:
        request = asyncio.create_task(
            client.post(
                "/api/v1/scan/extract-text",
                files={"image": ("label.png", png_image(), "image/png")},
            )
        )
        assert await asyncio.to_thread(worker_started.wait, 2)
        response = await request
    finally:
        release_worker.set()

    assert response.status_code == 504
    assert response.json() == {
        "success": False,
        "status": "failed",
        "error_code": "OCR_TIMEOUT",
        "message": "Scanning is taking longer than expected. Please try again.",
        "retryable": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_code"),
    [
        (OCRInitializationError("private model path"), 503, "OCR_MODEL_UNAVAILABLE"),
        (OCRInferenceError("private traceback"), 500, "OCR_PROCESSING_FAILED"),
    ],
)
async def test_ocr_engine_errors_hide_internal_details(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    def fail_extract(_: bytes, __: list[str]) -> list[OCRTextRegion]:
        raise failure

    monkeypatch.setattr("app.api.routes.ocr.ocr_runtime.extract_text", fail_extract)

    response = await client.post(
        "/api/v1/scan/extract-text",
        files={"image": ("label.png", png_image(), "image/png")},
    )

    assert response.status_code == expected_status
    assert response.json()["error_code"] == expected_code
    assert response.json()["retryable"] is True
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_retry_after_model_initialization_failure_succeeds(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_once(_: bytes, __: list[str]) -> list[OCRTextRegion]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OCRInitializationError("model initialization failed")
        return []

    monkeypatch.setattr("app.api.routes.ocr.ocr_runtime.extract_text", fail_once)
    files = {"image": ("label.png", png_image(), "image/png")}

    failed_response = await client.post("/api/v1/scan/extract-text", files=files)
    retry_response = await client.post("/api/v1/scan/extract-text", files=files)

    assert failed_response.status_code == 503
    assert retry_response.status_code == 200
    assert calls == 2
