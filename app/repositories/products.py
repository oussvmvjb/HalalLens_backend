from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ingredient, Product


async def product_by_barcode(db: AsyncSession, barcode: str) -> Product | None:
    result = await db.execute(select(Product).where(Product.barcode == barcode).limit(1))
    return result.scalar_one_or_none()


async def search_products(db: AsyncSession, query: str, limit: int, offset: int) -> list[Product]:
    statement = (
        select(Product)
        .where(or_(Product.name.ilike(f"%{query}%"), Product.brand.ilike(f"%{query}%")))
        .order_by(Product.name)
        .offset(offset)
        .limit(limit)
    )
    return list((await db.scalars(statement)).all())


async def search_ingredients(
    db: AsyncSession, query: str, limit: int, offset: int
) -> list[Ingredient]:
    statement = (
        select(Ingredient)
        .where(
            or_(
                Ingredient.canonical_name.ilike(f"%{query}%"),
                Ingredient.normalized_name.ilike(f"%{query}%"),
                Ingredient.ingredient_code.ilike(f"%{query}%"),
            )
        )
        .order_by(Ingredient.canonical_name)
        .offset(offset)
        .limit(limit)
    )
    return list((await db.scalars(statement)).all())
