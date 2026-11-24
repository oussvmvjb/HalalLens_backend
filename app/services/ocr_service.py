from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from difflib import SequenceMatcher
from functools import lru_cache
from io import BytesIO
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from app.schemas.ocr import OCRTextRegion

logger = logging.getLogger(__name__)

MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_EDGE = 3_200
SUPPORTED_OCR_LANGUAGES = ("en", "fr", "ar", "zh_CN")
DEFAULT_OCR_LANGUAGES = ("en", "fr", "ar")
OCR_PIPELINE_LANGUAGES = ("latin", "ar", "zh_CN")
_inference_lock = threading.Lock()


class OCRPipeline(Protocol):
    def predict(self, input: object) -> Iterable[object]: ...


class InvalidImageError(ValueError):
    pass


class UnsupportedImageError(InvalidImageError):
    pass


class ImageTooLargeError(InvalidImageError):
    pass


class OCRServiceError(RuntimeError):
    pass


class OCRInitializationError(OCRServiceError):
    pass


class OCRInferenceError(OCRServiceError):
    pass


def preprocess_image(image_bytes: bytes) -> object:
    try:
        with Image.open(BytesIO(image_bytes)) as opened_image:
            if opened_image.format not in {"JPEG", "PNG", "WEBP"}:
                raise UnsupportedImageError("Only JPEG, PNG, and WebP images are supported.")
            if opened_image.width * opened_image.height > MAX_IMAGE_PIXELS:
                raise ImageTooLargeError("Image pixel dimensions exceed the processing limit.")

            image = ImageOps.exif_transpose(opened_image)
            image.load()
            if image.mode in {"RGBA", "LA"}:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")

            image.thumbnail(
                (MAX_IMAGE_EDGE, MAX_IMAGE_EDGE),
                Image.Resampling.LANCZOS,
            )
            rgb_image = image.copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise InvalidImageError("The uploaded file is not a readable image.") from error

    try:
        import cv2
        import numpy as np

        rgb = np.asarray(rgb_image)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if float(np.std(gray)) < 45:
            lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
            lightness, channel_a, channel_b = cv2.split(lab)
            lightness = cv2.createCLAHE(
                clipLimit=1.5,
                tileGridSize=(8, 8),
            ).apply(lightness)
            bgr = cv2.cvtColor(
                cv2.merge((lightness, channel_a, channel_b)),
                cv2.COLOR_LAB2BGR,
            )
        return bgr
    except ImportError as error:
        logger.exception("OCR image preprocessing dependencies are unavailable.")
        raise OCRServiceError("OCR preprocessing dependencies are unavailable.") from error


@lru_cache(maxsize=3)
def _load_pipeline(language: str) -> OCRPipeline:
    model_configs = {
        "latin": (
            "PP-OCRv5_mobile_det",
            "latin_PP-OCRv5_mobile_rec",
        ),
        "ar": (
            "PP-OCRv5_mobile_det",
            "arabic_PP-OCRv5_mobile_rec",
        ),
        "zh_CN": (
            "PP-OCRv5_server_det",
            "PP-OCRv5_server_rec",
        ),
    }
    if language not in model_configs:
        raise ValueError(f"Unsupported OCR language: {language}")
    detection_model_name, recognition_model_name = model_configs[language]
    try:
        from paddleocr import PaddleOCR

        return PaddleOCR(
            text_detection_model_name=detection_model_name,
            text_recognition_model_name=recognition_model_name,
            device="cpu",
            enable_mkldnn=False,
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=True,
        )
    except MemoryError:
        raise
    except Exception as error:
        raise OCRInitializationError("PaddleOCR could not be initialized.") from error


def extract_text(
    image_bytes: bytes,
    languages: Iterable[str] | None = None,
) -> list[OCRTextRegion]:
    image = preprocess_image(image_bytes)
    _, pipeline_languages = _select_pipeline_languages(languages)
    return _run_pipelines(
        image,
        pipeline_languages,
        _load_pipeline,
    )


def _select_pipeline_languages(
    languages: Iterable[str] | None,
) -> tuple[tuple[str, ...], list[str]]:
    selected_languages = tuple(dict.fromkeys(languages or DEFAULT_OCR_LANGUAGES))
    unsupported_languages = set(selected_languages) - set(SUPPORTED_OCR_LANGUAGES)
    if not selected_languages or unsupported_languages:
        raise ValueError(f"Unsupported OCR languages: {sorted(unsupported_languages)}")

    pipeline_languages: list[str] = []
    if any(language in selected_languages for language in ("en", "fr")):
        pipeline_languages.append("latin")
    if "ar" in selected_languages:
        pipeline_languages.append("ar")
    if "zh_CN" in selected_languages:
        pipeline_languages.append("zh_CN")

    return selected_languages, pipeline_languages


def _run_pipelines(
    image: object,
    pipeline_languages: list[str],
    get_pipeline: Callable[[str], OCRPipeline],
) -> list[OCRTextRegion]:
    detections: list[OCRTextRegion] = []
    with _inference_lock:
        for language in pipeline_languages:
            try:
                for page in get_pipeline(language).predict(input=image):
                    detections.extend(_parse_page(page))
            except OCRServiceError:
                raise
            except MemoryError:
                raise
            except Exception as error:
                logger.exception("PaddleOCR inference failed for language %s.", language)
                raise OCRInferenceError("PaddleOCR could not process the image.") from error
    return _sort_regions(_deduplicate_regions(detections))


class OCRRuntime:
    def __init__(self) -> None:
        self._initialization_lock = threading.Lock()
        self._pipelines: dict[str, OCRPipeline] = {}
        self._initialized = False
        self._initialization_error: Exception | None = None

    def initialize(self) -> None:
        with self._initialization_lock:
            if self._initialized:
                return
            try:
                pipelines = {
                    language: _load_pipeline(language)
                    for language in OCR_PIPELINE_LANGUAGES
                }
            except Exception as error:
                self._pipelines.clear()
                self._initialization_error = error
                _load_pipeline.cache_clear()
                logger.exception("Could not initialize all PaddleOCR pipelines.")
                raise OCRInitializationError(
                    "PaddleOCR runtime initialization failed."
                ) from error

            self._pipelines = pipelines
            self._initialization_error = None
            self._initialized = True
            logger.info(
                "PaddleOCR pipelines initialized.",
                extra={"pipeline_count": len(pipelines)},
            )

    def extract_text(
        self,
        image_bytes: bytes,
        languages: Iterable[str] | None = None,
    ) -> list[OCRTextRegion]:
        image = preprocess_image(image_bytes)
        _, pipeline_languages = _select_pipeline_languages(languages)
        if not self._initialized:
            raise OCRInitializationError(
                "The PaddleOCR runtime has not been initialized."
            ) from self._initialization_error
        return _run_pipelines(
            image,
            pipeline_languages,
            self._get_pipeline,
        )

    def _get_pipeline(self, language: str) -> OCRPipeline:
        try:
            return self._pipelines[language]
        except KeyError as error:
            raise OCRInitializationError(
                "A requested PaddleOCR pipeline is unavailable."
            ) from error

    def close(self) -> None:
        with self._initialization_lock:
            with _inference_lock:
                self._pipelines.clear()
                self._initialized = False
                self._initialization_error = None
                _load_pipeline.cache_clear()


ocr_runtime = OCRRuntime()


def _parse_page(page: object) -> list[OCRTextRegion]:
    payload: object = getattr(page, "json", page)
    if callable(payload):
        payload = payload()
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, Mapping):
        raise OCRServiceError("PaddleOCR returned an unsupported result format.")

    result = payload.get("res", payload)
    if not isinstance(result, Mapping):
        raise OCRServiceError("PaddleOCR returned an unsupported result payload.")

    texts = result.get("rec_texts")
    confidences = result.get("rec_scores")
    boxes = result.get("rec_polys", result.get("dt_polys"))
    if not isinstance(texts, (list, tuple)) or not isinstance(confidences, (list, tuple)):
        raise OCRServiceError("PaddleOCR response is missing recognized text or confidence.")

    parsed_boxes = _as_boxes(boxes)
    regions: list[OCRTextRegion] = []
    for index, text in enumerate(texts):
        if not isinstance(text, str) or not text.strip():
            continue
        if index >= len(confidences):
            raise OCRServiceError("PaddleOCR response has an incomplete confidence list.")
        confidence = float(confidences[index])
        box = parsed_boxes[index] if index < len(parsed_boxes) else []
        regions.append(
            OCRTextRegion(
                text=text,
                confidence=confidence,
                bounding_box=box,
            )
        )
    return regions


def _as_boxes(value: object) -> list[list[list[float]]]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        return []

    boxes: list[list[list[float]]] = []
    for raw_box in value:
        if hasattr(raw_box, "tolist"):
            raw_box = raw_box.tolist()
        if not isinstance(raw_box, (list, tuple)):
            boxes.append([])
            continue
        points: list[list[float]] = []
        for raw_point in raw_box:
            if hasattr(raw_point, "tolist"):
                raw_point = raw_point.tolist()
            if not isinstance(raw_point, (list, tuple)):
                points = []
                break
            points.append([float(coordinate) for coordinate in raw_point])
        boxes.append(points)
    return boxes


def _sort_regions(detections: list[OCRTextRegion]) -> list[OCRTextRegion]:
    return sorted(
        detections,
        key=lambda region: (
            min((point[1] for point in region.bounding_box), default=0.0),
            min((point[0] for point in region.bounding_box), default=0.0),
        ),
    )


def _deduplicate_regions(detections: list[OCRTextRegion]) -> list[OCRTextRegion]:
    unique_regions: list[OCRTextRegion] = []
    for candidate in sorted(detections, key=lambda region: region.confidence, reverse=True):
        if any(_are_duplicate_regions(candidate, accepted) for accepted in unique_regions):
            continue
        unique_regions.append(candidate)
    return unique_regions


def _are_duplicate_regions(first: OCRTextRegion, second: OCRTextRegion) -> bool:
    overlap = _bounding_box_iou(first.bounding_box, second.bounding_box)
    if overlap < 0.65:
        return False

    first_text = _normalized_region_text(first.text)
    second_text = _normalized_region_text(second.text)
    if first_text == second_text:
        return True
    if min(len(first_text), len(second_text)) < 5:
        return False
    return SequenceMatcher(None, first_text, second_text).ratio() >= 0.84


def _normalized_region_text(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())


def _bounding_box_iou(
    first: list[list[float]],
    second: list[list[float]],
) -> float:
    if not first or not second:
        return 0.0

    first_left = min(point[0] for point in first)
    first_top = min(point[1] for point in first)
    first_right = max(point[0] for point in first)
    first_bottom = max(point[1] for point in first)
    second_left = min(point[0] for point in second)
    second_top = min(point[1] for point in second)
    second_right = max(point[0] for point in second)
    second_bottom = max(point[1] for point in second)

    intersection_width = max(0.0, min(first_right, second_right) - max(first_left, second_left))
    intersection_height = max(0.0, min(first_bottom, second_bottom) - max(first_top, second_top))
    intersection = intersection_width * intersection_height
    first_area = max(0.0, first_right - first_left) * max(0.0, first_bottom - first_top)
    second_area = max(0.0, second_right - second_left) * max(0.0, second_bottom - second_top)
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0
