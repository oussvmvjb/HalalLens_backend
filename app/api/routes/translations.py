import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import DatabaseSession
from app.schemas.product_translation import (
    ProductDetailsTranslationRequest,
    ProductDetailsTranslationResponse,
    ProductTranslationFailure,
)
from app.services.product_translation import (
    ProductTranslationUnavailable,
    UnsupportedTranslationLanguage,
    translate_product_details,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/translations", tags=["Product translation"])


@router.post(
    "/product-details",
    response_model=ProductDetailsTranslationResponse,
    responses={
        400: {"model": ProductTranslationFailure},
        503: {"model": ProductTranslationFailure},
    },
)
async def translate_details(
    payload: ProductDetailsTranslationRequest,
    db: DatabaseSession,
) -> ProductDetailsTranslationResponse | JSONResponse:
    try:
        return await translate_product_details(db, payload)
    except UnsupportedTranslationLanguage:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ProductTranslationFailure(
                error_code="UNSUPPORTED_LANGUAGE",
                message="The requested target language is not supported.",
                retryable=False,
            ).model_dump(),
        )
    except ProductTranslationUnavailable:
        logger.warning("Product details translation is unavailable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ProductTranslationFailure(
                error_code="TRANSLATION_UNAVAILABLE",
                message="Translation is temporarily unavailable. Please try again.",
                retryable=True,
            ).model_dump(),
        )
