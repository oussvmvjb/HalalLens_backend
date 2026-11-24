import io
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType

import pytest
from PIL import Image, ImageFilter

from app.schemas.ocr import OCRTextRegion
from app.services import ocr_service
from app.services.ingredient_processing import extract_ingredients
from app.services.ocr_service import (
    OCRInferenceError,
    OCRInitializationError,
    OCRRuntime,
    _load_pipeline,
)


def test_load_pipeline_disables_mkldnn(monkeypatch) -> None:
    arguments: dict[str, object] = {}

    def fake_paddle_ocr(**kwargs: object) -> object:
        arguments.update(kwargs)
        return object()

    paddleocr = ModuleType("paddleocr")
    setattr(paddleocr, "PaddleOCR", fake_paddle_ocr)
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)
    _load_pipeline.cache_clear()
    try:
        _load_pipeline("latin")
    finally:
        _load_pipeline.cache_clear()

    assert arguments["device"] == "cpu"
    assert arguments["enable_mkldnn"] is False
    assert arguments["text_recognition_model_name"] == "latin_PP-OCRv5_mobile_rec"


def test_load_chinese_pipeline_uses_paddleocr_37_server_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments: dict[str, object] = {}

    def fake_paddle_ocr(**kwargs: object) -> object:
        arguments.update(kwargs)
        return object()

    paddleocr = ModuleType("paddleocr")
    setattr(paddleocr, "PaddleOCR", fake_paddle_ocr)
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)
    _load_pipeline.cache_clear()
    try:
        _load_pipeline("zh_CN")
    finally:
        _load_pipeline.cache_clear()

    assert arguments["text_detection_model_name"] == "PP-OCRv5_server_det"
    assert arguments["text_recognition_model_name"] == "PP-OCRv5_server_rec"
    assert arguments["device"] == "cpu"
    assert arguments["enable_mkldnn"] is False


def test_load_pipeline_initializes_and_caches_a_model_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    pipeline = object()

    def fake_paddle_ocr(**_: object) -> object:
        nonlocal calls
        calls += 1
        return pipeline

    paddleocr = ModuleType("paddleocr")
    setattr(paddleocr, "PaddleOCR", fake_paddle_ocr)
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)
    _load_pipeline.cache_clear()
    try:
        first = _load_pipeline("latin")
        second = _load_pipeline("latin")
    finally:
        _load_pipeline.cache_clear()

    assert first is second is pipeline
    assert calls == 1


def test_ocr_runtime_initializes_models_once_and_reinitializes_after_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_paddle_ocr(**arguments: object) -> FakePipeline:
        calls.append(str(arguments["text_recognition_model_name"]))
        return FakePipeline([])

    paddleocr = ModuleType("paddleocr")
    setattr(paddleocr, "PaddleOCR", fake_paddle_ocr)
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)
    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    _load_pipeline.cache_clear()
    runtime = OCRRuntime()
    try:
        runtime.initialize()
        runtime.initialize()
        runtime.extract_text(b"image", languages=["zh_CN"])
        assert calls == [
            "latin_PP-OCRv5_mobile_rec",
            "arabic_PP-OCRv5_mobile_rec",
            "PP-OCRv5_server_rec",
        ]

        runtime.close()
        runtime.initialize()
        assert len(calls) == 6
    finally:
        runtime.close()


def test_ocr_runtime_does_not_retry_failed_startup_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def failed_paddle_ocr(**_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("private initialization details")

    paddleocr = ModuleType("paddleocr")
    setattr(paddleocr, "PaddleOCR", failed_paddle_ocr)
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)
    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    _load_pipeline.cache_clear()
    runtime = OCRRuntime()
    try:
        with pytest.raises(OCRInitializationError):
            runtime.initialize()
        with pytest.raises(OCRInitializationError):
            runtime.extract_text(b"image")
        assert calls == 1
    finally:
        runtime.close()


class FakePipeline:
    def __init__(self, pages: list[object]) -> None:
        self.pages = pages
        self.calls = 0

    def predict(self, input: object) -> list[object]:
        self.calls += 1
        return self.pages


def _page(
    text: str,
    confidence: float,
    box: list[list[float]],
) -> dict[str, object]:
    return {
        "rec_texts": [text],
        "rec_scores": [confidence],
        "rec_polys": [box],
    }


def test_extract_text_deduplicates_overlapping_similar_detections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latin = FakePipeline(
        [_page("ORGANIC LIQUID", 0.96, [[0, 0], [100, 0], [100, 20], [0, 20]])]
    )
    arabic = FakePipeline(
        [_page("ORGANIC LIOUID", 0.91, [[1, 0], [101, 0], [101, 20], [1, 20]])]
    )
    pipelines = {"latin": latin, "ar": arabic}
    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    monkeypatch.setattr(ocr_service, "_load_pipeline", lambda language: pipelines[language])

    regions = ocr_service.extract_text(b"image")

    assert [region.text for region in regions] == ["ORGANIC LIQUID"]
    assert regions[0].confidence == 0.96
    assert latin.calls == 1
    assert arabic.calls == 1


def test_extract_text_preserves_repeated_text_at_distinct_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latin = FakePipeline(
        [
            _page("SOAP", 0.96, [[0, 0], [40, 0], [40, 12], [0, 12]]),
            _page("SOAP", 0.91, [[0, 30], [40, 30], [40, 42], [0, 42]]),
        ]
    )
    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    monkeypatch.setattr(ocr_service, "_load_pipeline", lambda _: latin)

    regions = ocr_service.extract_text(b"image", languages=["en", "fr"])

    assert [region.text for region in regions] == ["SOAP", "SOAP"]
    assert latin.calls == 1


def test_extract_text_preserves_accents_and_arabic_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latin = FakePipeline(
        [_page("INGRÉDIENTS", 0.95, [[0, 0], [60, 0], [60, 15], [0, 15]])]
    )
    arabic = FakePipeline(
        [_page("المكونات", 0.93, [[0, 30], [60, 30], [60, 45], [0, 45]])]
    )
    pipelines = {"latin": latin, "ar": arabic}
    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    monkeypatch.setattr(ocr_service, "_load_pipeline", lambda language: pipelines[language])

    regions = ocr_service.extract_text(b"image", languages=["fr", "ar"])

    assert [region.text for region in regions] == ["INGRÉDIENTS", "المكونات"]


def test_extract_text_supports_mixed_chinese_and_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latin = FakePipeline(
        [_page("AQUA", 0.96, [[80, 0], [130, 0], [130, 18], [80, 18]])]
    )
    chinese = FakePipeline(
        [
            _page("配料表：水，甘油", 0.95, [[0, 0], [70, 0], [70, 18], [0, 18]]),
            _page("AQUA", 0.92, [[80, 0], [130, 0], [130, 18], [80, 18]]),
        ]
    )
    pipelines = {"latin": latin, "zh_CN": chinese}
    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    monkeypatch.setattr(ocr_service, "_load_pipeline", lambda language: pipelines[language])

    regions = ocr_service.extract_text(b"image", languages=["zh_CN", "en"])

    assert [region.text for region in regions] == ["配料表：水，甘油", "AQUA"]
    assert latin.calls == 1
    assert chinese.calls == 1


@pytest.mark.parametrize(
    ("languages", "expected_pipelines"),
    [
        (["en"], ["latin"]),
        (["fr"], ["latin"]),
        (["en", "fr"], ["latin"]),
        (["ar"], ["ar"]),
        (["zh_CN"], ["zh_CN"]),
        (["zh_CN", "en"], ["latin", "zh_CN"]),
    ],
)
def test_extract_text_selects_configured_language_models(
    monkeypatch: pytest.MonkeyPatch,
    languages: list[str],
    expected_pipelines: list[str],
) -> None:
    loaded: list[str] = []

    def load_pipeline(language: str) -> FakePipeline:
        loaded.append(language)
        return FakePipeline([])

    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    monkeypatch.setattr(ocr_service, "_load_pipeline", load_pipeline)

    ocr_service.extract_text(b"image", languages=languages)

    assert loaded == expected_pipelines


def test_concurrent_extraction_serializes_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    maximum_active = 0
    guard = threading.Lock()

    class ConcurrentPipeline:
        def predict(self, input: object) -> list[object]:
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return []

    pipeline = ConcurrentPipeline()
    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    monkeypatch.setattr(ocr_service, "_load_pipeline", lambda _: pipeline)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: ocr_service.extract_text(b"image"), range(2)))

    assert results == [[], []]
    assert maximum_active == 1


def test_inference_exception_is_classified_for_api_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedPipeline:
        def predict(self, input: object) -> list[object]:
            raise RuntimeError("private inference detail")

    monkeypatch.setattr(ocr_service, "preprocess_image", lambda _: object())
    monkeypatch.setattr(ocr_service, "_load_pipeline", lambda _: FailedPipeline())

    with pytest.raises(OCRInferenceError):
        ocr_service.extract_text(b"image", languages=["en"])


def test_preprocess_image_applies_exif_orientation() -> None:
    image = Image.new("RGB", (20, 40), "white")
    exif = Image.Exif()
    exif[274] = 6
    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif)

    processed = ocr_service.preprocess_image(output.getvalue())

    assert processed.shape[:2] == (20, 40)


@pytest.mark.parametrize(
    "image",
    [
        Image.new("RGB", (120, 60), "white").filter(ImageFilter.GaussianBlur(radius=2)),
        Image.new("RGB", (24, 12), "white"),
    ],
)
def test_preprocess_image_accepts_blurry_and_small_labels(image: Image.Image) -> None:
    output = io.BytesIO()
    image.save(output, format="PNG")

    processed = ocr_service.preprocess_image(output.getvalue())

    assert processed.size > 0


def test_extract_ingredients_preserves_order_and_marks_correction_for_review() -> None:
    regions = [
        OCRTextRegion(
            text=(
                "Ingredients: AQUA, POTASSIUM OLIVATE, GLYCERIN, "
                "CITRUS NOBILIS PEEL OIL EXPRESSED, "
                "CITRUS SINENSIS PEEL OIL EXPRESSED, SODIUM CHLORIDE, GERANIOL, "
                "LIMONNE, LINALOOL, Warnings: avoid contact with eyes"
            ),
            confidence=0.92,
            bounding_box=[],
        ),
        OCRTextRegion(text="Recycling information", confidence=0.9),
    ]

    ingredients, warnings = extract_ingredients(regions)

    assert [ingredient.original_text for ingredient in ingredients] == [
        "AQUA",
        "POTASSIUM OLIVATE",
        "GLYCERIN",
        "CITRUS NOBILIS PEEL OIL EXPRESSED",
        "CITRUS SINENSIS PEEL OIL EXPRESSED",
        "SODIUM CHLORIDE",
        "GERANIOL",
        "LIMONNE",
        "LINALOOL",
    ]
    assert ingredients[7].normalized_text == "LIMONENE"
    assert ingredients[7].normalization_confidence >= 0.78
    assert ingredients[7].needs_review
    assert ingredients[0].normalized_text == "AQUA"
    assert not ingredients[0].needs_review
    assert warnings == []


@pytest.mark.parametrize(
    "header",
    ["Ingrédients :", "المكونات:"],
)
def test_extract_ingredients_supports_french_and_arabic_headers(header: str) -> None:
    ingredients, warnings = extract_ingredients(
        [OCRTextRegion(text=f"{header} Aqua, Glycerin", confidence=0.9)]
    )

    assert [ingredient.original_text for ingredient in ingredients] == ["Aqua", "Glycerin"]
    assert warnings == []


def test_extract_ingredients_preserves_chinese_names_without_translation() -> None:
    ingredients, warnings = extract_ingredients(
        [
            OCRTextRegion(
                text="配料表：水，甘油，柠檬烯，未知植物提取物",
                confidence=0.93,
            )
        ]
    )

    assert [ingredient.original_text for ingredient in ingredients] == [
        "水",
        "甘油",
        "柠檬烯",
        "未知植物提取物",
    ]
    assert [ingredient.normalized_text for ingredient in ingredients] == [
        "水",
        "甘油",
        "柠檬烯",
        None,
    ]
    assert ingredients[3].needs_review
    assert warnings


def test_extract_ingredients_stops_at_chinese_warning_heading() -> None:
    ingredients, warnings = extract_ingredients(
        [
            OCRTextRegion(
                text="配料表：水、甘油。警告：避免接触眼睛",
                confidence=0.93,
            )
        ]
    )

    assert [ingredient.original_text for ingredient in ingredients] == ["水", "甘油"]
    assert warnings == []


def test_extract_ingredients_warns_when_section_is_missing() -> None:
    ingredients, warnings = extract_ingredients(
        [OCRTextRegion(text="Organic liquid soap, handmade", confidence=0.9)]
    )

    assert ingredients == []
    assert warnings == ["Ingredient section could not be identified confidently."]


def test_extract_ingredients_does_not_suggest_unknown_names() -> None:
    ingredients, warnings = extract_ingredients(
        [OCRTextRegion(text="Ingredients: UNKNOWN BOTANICAL", confidence=0.8)]
    )

    assert len(ingredients) == 1
    assert ingredients[0].original_text == "UNKNOWN BOTANICAL"
    assert ingredients[0].normalized_text is None
    assert ingredients[0].needs_review
    assert warnings
