import asyncio
import logging
import math
import os
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.schemas.ocr import (
    OCRExtractionData,
    OCRExtractionResponse,
    OCRFailureResponse,
    OCRLanguage,
)
from app.services.ocr_service import (
    DEFAULT_OCR_LANGUAGES,
    ImageTooLargeError,
    InvalidImageError,
    OCRInferenceError,
    OCRInitializationError,
    OCRServiceError,
    UnsupportedImageError,
    ocr_runtime,
)
from app.services.ingredient_processing import extract_ingredients

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scan", tags=["Text extraction"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
OCR_PROCESSING_TIMEOUT_SECONDS = float(
    os.getenv("OCR_PROCESSING_TIMEOUT_SECONDS", "600")
)
if not math.isfinite(OCR_PROCESSING_TIMEOUT_SECONDS) or OCR_PROCESSING_TIMEOUT_SECONDS <= 0:
    raise ValueError("OCR_PROCESSING_TIMEOUT_SECONDS must be a positive finite number.")


def _failure(
    status_code: int,
    error_code: str,
    message: str,
    *,
    retryable: bool,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=OCRFailureResponse(
            error_code=error_code,
            message=message,
            retryable=retryable,
        ).model_dump(),
    )


def _consume_late_result(future: asyncio.Future[object]) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("An OCR worker failed after its request ended.")


@router.post(
    "/extract-text",
    response_model=OCRExtractionResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": OCRFailureResponse},
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {"model": OCRFailureResponse},
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"model": OCRFailureResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": OCRFailureResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": OCRFailureResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": OCRFailureResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": OCRFailureResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": OCRFailureResponse},
    },
)
async def extract_product_text(
    image: Annotated[UploadFile, File(description="Product label image")],
    request: Request,
    languages: Annotated[list[OCRLanguage] | None, Form()] = None,
) -> OCRExtractionResponse | JSONResponse:
    worker: asyncio.Future[list] | None = None
    try:
        content = await image.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return _failure(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "IMAGE_TOO_LARGE",
                "The image is too large. Choose an image under 10 MiB.",
                retryable=False,
            )
        if not content:
            return _failure(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "EMPTY_IMAGE",
                "The uploaded image is empty. Choose another image.",
                retryable=False,
            )

        selected_languages = list(dict.fromkeys(languages or DEFAULT_OCR_LANGUAGES))
        processing_started = perf_counter()
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(
            None,
            request.app.state.ocr_runtime.extract_text,
            content,
            selected_languages,
        )
        try:
            regions = await asyncio.wait_for(
                asyncio.shield(worker),
                timeout=OCR_PROCESSING_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            worker.add_done_callback(_consume_late_result)
            logger.warning(
                "OCR request exceeded its %.1f second processing limit.",
                OCR_PROCESSING_TIMEOUT_SECONDS,
                extra={
                    "processing_duration_ms": round(
                        (perf_counter() - processing_started) * 1000
                    ),
                    "languages": selected_languages,
                },
            )
            return _failure(
                status.HTTP_504_GATEWAY_TIMEOUT,
                "OCR_TIMEOUT",
                "Scanning is taking longer than expected. Please try again.",
                retryable=True,
            )

        ingredients, warnings = extract_ingredients(regions)
        logger.info(
            "OCR request completed.",
            extra={
                "processing_duration_ms": round(
                    (perf_counter() - processing_started) * 1000
                ),
                "languages": selected_languages,
                "region_count": len(regions),
            },
        )
        return OCRExtractionResponse(
            success=True,
            data=OCRExtractionData(extracted_text=regions),
            full_text="\n".join(region.text for region in regions),
            extracted_text=regions,
            language=selected_languages,
            ingredients=ingredients,
            warnings=warnings,
        )
    except asyncio.CancelledError:
        if worker is not None:
            worker.add_done_callback(_consume_late_result)
        logger.info("OCR request was cancelled by the client.")
        raise
    except UnsupportedImageError:
        return _failure(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "UNSUPPORTED_IMAGE_FORMAT",
            "Choose a JPG, PNG, or WebP image.",
            retryable=False,
        )
    except ImageTooLargeError:
        return _failure(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "IMAGE_TOO_LARGE",
            "The image dimensions are too large to process.",
            retryable=False,
        )
    except InvalidImageError:
        return _failure(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "INVALID_IMAGE",
            "The uploaded file is not a readable image. Choose another image.",
            retryable=False,
        )
    except OCRInitializationError:
        return _failure(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "OCR_MODEL_UNAVAILABLE",
            "The scanner is temporarily unavailable. Please try again.",
            retryable=True,
        )
    except OCRInferenceError:
        return _failure(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "OCR_PROCESSING_FAILED",
            "We couldn't read this image. Please try again.",
            retryable=True,
        )
    except MemoryError:
        logger.exception("The OCR request ran out of memory.")
        return _failure(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "OCR_RESOURCE_EXHAUSTED",
            "The scanner is busy. Please try again shortly.",
            retryable=True,
        )
    except OCRServiceError:
        return _failure(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "OCR_ENGINE_UNAVAILABLE",
            "The scanner is temporarily unavailable. Please try again.",
            retryable=True,
        )
    except Exception:
        logger.exception("Unexpected error while processing an OCR request.")
        return _failure(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "We couldn't read this image. Please try again.",
            retryable=True,
        )
