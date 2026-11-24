from typing import Annotated
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from app.api.dependencies.auth import DatabaseSession
from app.models import Product
from app.repositories.products import product_by_barcode, search_products
from app.schemas.api import ProductCreate, ProductResponse

router = APIRouter(prefix="/products", tags=["Products"])


@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate, db: DatabaseSession) -> Product:
    product = Product(**payload.model_dump(mode="json"))
    db.add(product)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A product with this barcode already exists.",
        ) from error
    await db.refresh(product)
    return product


@router.get("/search", response_model=list[ProductResponse])
async def search(
    db: DatabaseSession,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Product]:
    return await search_products(db, q.strip(), limit, offset)


@router.get("/{barcode}", response_model=ProductResponse)
async def get_product(barcode: str, db: DatabaseSession) -> Product:
    product = await product_by_barcode(db, barcode)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product
