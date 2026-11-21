
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, certifications, ingredients, products, reports, scans
from app.core.config import get_settings
from app.db.session import engine
from app.db.base import Base  # Imports Base and registers all models

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Automatically create all database tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("Database tables created successfully.")

    yield

    # Close database connections on shutdown
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="HalalLens product, scan, and ingredient API foundation.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix=settings.api_v1_prefix)

api_router.include_router(auth.router)
api_router.include_router(products.router)
api_router.include_router(scans.router)
api_router.include_router(ingredients.router)
api_router.include_router(certifications.router)
api_router.include_router(reports.router)

app.include_router(api_router)


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
