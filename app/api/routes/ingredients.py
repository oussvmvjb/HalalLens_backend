from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.dependencies.auth import DatabaseSession
from app.models import Ingredient
from app.repositories.products import search_ingredients
from app.schemas.api import IngredientResponse

router = APIRouter(prefix="/ingredients", tags=["Ingredients"])


@router.get("/", response_model=list[IngredientResponse])
async def list_ingredients(
    db: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Ingredient]:
    result = await db.scalars(
        select(Ingredient).order_by(Ingredient.canonical_name).offset(offset).limit(limit)
    )
    return list(result.all())


@router.get("/search", response_model=list[IngredientResponse])
async def search(
    db: DatabaseSession,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Ingredient]:
    return await search_ingredients(db, q.strip(), limit, offset)


@router.get("/{ingredient_id}", response_model=IngredientResponse)
async def get_ingredient(ingredient_id: UUID, db: DatabaseSession) -> Ingredient:
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found")
    return ingredient
