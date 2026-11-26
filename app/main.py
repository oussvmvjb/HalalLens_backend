
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool

from app.api.routes import (
    auth,
    barcode,
    certifications,
    halal,
    ingredients,
    ocr,
    products,
    reports,
    scans,
    translations,
)
from app.core.config import get_settings
from app.db.init_db import initialize_database, logger as db_logger
from app.db.session import SessionLocal, engine
from app.schemas.ocr import OCRFailureResponse
from app.schemas.barcode import BarcodeLookupFailure
from app.services.ocr_service import OCRInitializationError, ocr_runtime

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await initialize_database()
    try:
        from scripts.seed_halal_reference_dataset import run_import

        async with SessionLocal() as seed_session:
            stats = await run_import(seed_session)
            if stats["imported"] > 0:
                db_logger.info(
                    "[DATABASE] Seeded %d ingredient knowledge base records.",
                    stats["imported"],
                )
    except Exception as exc:
        db_logger.warning("[DATABASE] Ingredient reference seed skipped: %s", exc)

    app.state.ocr_runtime = ocr_runtime
    app.state.ocr_initialization_failed = False
    try:
        await run_in_threadpool(ocr_runtime.initialize)
    except OCRInitializationError:
        app.state.ocr_initialization_failed = True

    db_logger.info("[DATABASE] Backend startup completed.")

    try:
        yield
    finally:
        await run_in_threadpool(ocr_runtime.close)
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="HalalLens product, scan, and ingredient API foundation.",
    lifespan=lifespan,
)
app.state.ocr_runtime = ocr_runtime

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix=settings.api_v1_prefix)

api_router.include_router(auth.router)
api_router.include_router(ocr.router)
api_router.include_router(barcode.router)
api_router.include_router(halal.router)
api_router.include_router(products.router)
api_router.include_router(scans.router)
api_router.include_router(ingredients.router)
api_router.include_router(certifications.router)
api_router.include_router(reports.router)
api_router.include_router(translations.router)

app.include_router(api_router)


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    if request.method == "POST" and request.url.path.endswith("/scan/extract-text"):
        return JSONResponse(
            status_code=422,
            content=OCRFailureResponse(
                error_code="INVALID_OCR_REQUEST",
                message="The image request is incomplete or invalid.",
                retryable=False,
            ).model_dump(),
        )
    if request.method == "POST" and request.url.path.endswith("/scan/barcode"):
        return JSONResponse(
            status_code=422,
            content=BarcodeLookupFailure(
                error_code="INVALID_BARCODE",
                message="Enter a valid product barcode and try again.",
                retryable=False,
            ).model_dump(),
        )
    return await request_validation_exception_handler(request, error)


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
