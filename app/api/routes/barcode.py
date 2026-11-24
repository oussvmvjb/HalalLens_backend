from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from app.api.dependencies.auth import DatabaseSession, OptionalUser
from app.schemas.barcode import (
    BarcodeLookupFailure,
    BarcodeLookupRequest,
    BarcodeLookupResponse,
)
from app.services.barcode_lookup import (
    OpenFoodFactsInvalidResponse,
    OpenFoodFactsUnavailable,
    barcode_lookup_service,
    logger,
)

router = APIRouter(prefix="/scan", tags=["Barcode lookup"])


def _failure(
    status_code: int,
    error_code: str,
    message: str,
    retryable: bool,
) -> JSONResponse:
    response = BarcodeLookupFailure(
        error_code=error_code,
        message=message,
        retryable=retryable,
    )
    return JSONResponse(status_code=status_code, content=response.model_dump())


@router.post(
    "/barcode",
    response_model=BarcodeLookupResponse,
    responses={
        404: {"model": BarcodeLookupFailure},
        422: {"model": BarcodeLookupFailure},
        500: {"model": BarcodeLookupFailure},
        502: {"model": BarcodeLookupFailure},
        503: {"model": BarcodeLookupFailure},
    },
)
async def lookup_barcode(
    payload: BarcodeLookupRequest, db: DatabaseSession, user: OptionalUser
) -> JSONResponse | BarcodeLookupResponse:
    try:
        result = await barcode_lookup_service.lookup(db, payload.barcode, user)
    except OpenFoodFactsUnavailable:
        logger.exception("Open Food Facts is unavailable for barcode lookup")
        return _failure(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PRODUCT_LOOKUP_UNAVAILABLE",
            "Product information is temporarily unavailable. Please try again.",
            True,
        )
    except OpenFoodFactsInvalidResponse:
        logger.exception("Open Food Facts returned an invalid response")
        return _failure(
            status.HTTP_502_BAD_GATEWAY,
            "PRODUCT_SOURCE_INVALID",
            "Product information could not be verified. Please try again.",
            True,
        )
    except Exception:
        logger.exception("Barcode lookup failed")
        return _failure(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "BARCODE_LOOKUP_FAILED",
            "We couldn't retrieve this product. Please try again.",
            True,
        )

    if result is None:
        return _failure(
            status.HTTP_404_NOT_FOUND,
            "PRODUCT_NOT_FOUND",
            "No product was found for this barcode.",
            False,
        )
    return result
