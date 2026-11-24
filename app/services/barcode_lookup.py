import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CertificationIssuer,
    HalalCertification,
    Product,
    ProductIngredient,
    Scan,
    ScanStatus,
    User,
)
from app.schemas.barcode import (
    BarcodeAnalysisResponse,
    BarcodeCertificationResponse,
    BarcodeLookupResponse,
    BarcodeProductResponse,
)
from app.services.open_food_facts import (
    OpenFoodFactsInvalidResponse,
    OpenFoodFactsProduct,
    OpenFoodFactsUnavailable,
    fetch_open_food_facts_product,
)
from app.services.scans import analyze_scan

logger = logging.getLogger(__name__)


@dataclass
class _BarcodeLock:
    lock: asyncio.Lock
    references: int = 0


class BarcodeLookupService:
    def __init__(self) -> None:
        self._locks: dict[str, _BarcodeLock] = {}
        self._lock_registry_guard = threading.Lock()

    @asynccontextmanager
    async def _lock_for(self, barcode: str) -> AsyncIterator[None]:
        with self._lock_registry_guard:
            entry = self._locks.get(barcode)
            if entry is None:
                entry = _BarcodeLock(asyncio.Lock())
                self._locks[barcode] = entry
            entry.references += 1
        try:
            async with entry.lock:
                yield
        finally:
            with self._lock_registry_guard:
                entry.references -= 1
                if entry.references == 0:
                    self._locks.pop(barcode, None)

    async def _get_or_import_product(
        self, db: AsyncSession, barcode: str
    ) -> Product | None:
        async with self._lock_for(barcode):
            product = await db.scalar(
                select(Product)
                .where(Product.barcode == barcode)
                .limit(1)
            )
            if product is not None:
                return product

            await db.rollback()
            off_product = await fetch_open_food_facts_product(barcode)
            if off_product is None:
                return None

            product = await db.scalar(
                select(Product)
                .where(Product.barcode == barcode)
                .limit(1)
            )
            if product is not None:
                await db.rollback()
                return product

            product = _product_record(off_product)
            db.add(product)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                product = await db.scalar(
                    select(Product)
                    .where(Product.barcode == barcode)
                    .limit(1)
                )
                if product is None:
                    raise
                return product
            await db.refresh(product)
            return product

    async def lookup(
        self, db: AsyncSession, barcode: str, user: User | None
    ) -> BarcodeLookupResponse | None:
        user_id = user.id if user else None
        product = await self._get_or_import_product(db, barcode)
        if product is None:
            return None

        ingredients = await _product_ingredients(db, product)
        scan = Scan(
            user_id=user_id,
            product_id=product.id,
            barcode=barcode,
            scan_status=ScanStatus.RECEIVED,
        )
        db.add(scan)
        await db.flush()
        analysis = await analyze_scan(
            db,
            scan,
            original_ingredients_text=(
                product.external_data.get("ingredients_text")
                if isinstance(product.external_data.get("ingredients_text"), str)
                else ", ".join(ingredients) or None
            ),
            detected_language=None,
        )
        certifications = await _certifications_for_product(db, product.id)
        product_data = product.external_data or {}
        return BarcodeLookupResponse(
            product=BarcodeProductResponse(
                id=product.id,
                barcode=product.barcode,
                name=_nullable_external_string(product_data, "product_name")
                or (
                    product.name
                    if product.source != "open_food_facts"
                    else None
                ),
                brand=product.brand,
                image_url=product.image_url,
                source=product.source,
                ingredients=ingredients,
                ingredients_text=_nullable_external_string(
                    product_data, "ingredients_text"
                ),
                categories=_external_string_list(product_data, "categories"),
                country_of_origin=product.country_of_origin,
                origin_description=_nullable_external_string(
                    product_data, "origin_description"
                ),
                labels=_external_string_list(product_data, "labels"),
                nutriments=_external_nutriments(product_data),
            ),
            analysis=BarcodeAnalysisResponse(
                scan_id=scan.id,
                overall_status=analysis.overall_status,
                confidence_score=analysis.confidence_score,
                explanation=analysis.explanation,
            ),
            certifications=certifications,
        )


def _product_record(product: OpenFoodFactsProduct) -> Product:
    external_data = product.as_external_data()
    return Product(
        barcode=product.barcode,
        name=(product.name or "Product information unavailable")[:255],
        brand=product.brand[:160] if product.brand else None,
        image_url=product.image_url,
        source="open_food_facts",
        external_data=external_data,
    )


async def _product_ingredients(db: AsyncSession, product: Product) -> list[str]:
    values = product.external_data.get("ingredients")
    if isinstance(values, list):
        ingredients = [value for value in values if isinstance(value, str)]
        if ingredients:
            return ingredients
    rows = await db.scalars(
        select(ProductIngredient)
        .where(ProductIngredient.product_id == product.id)
        .order_by(ProductIngredient.position)
    )
    return [ingredient.original_name for ingredient in rows]


async def _certifications_for_product(
    db: AsyncSession, product_id: UUID
) -> list[BarcodeCertificationResponse]:
    rows = await db.execute(
        select(HalalCertification, CertificationIssuer)
        .join(CertificationIssuer, CertificationIssuer.id == HalalCertification.issuer_id)
        .where(HalalCertification.product_id == product_id)
    )
    return [
        BarcodeCertificationResponse(
            issuer=issuer.name,
            issuer_authority_status=issuer.authority_status,
            certificate_number=certification.certificate_number,
            expiry_date=certification.expiry_date,
            verification_status=certification.verification_status,
            evidence_url=certification.evidence_url,
        )
        for certification, issuer in rows
    ]


def _nullable_external_string(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _external_string_list(data: dict[str, object], key: str) -> list[str]:
    values = data.get(key)
    if isinstance(values, list):
        return [value for value in values if isinstance(value, str)]
    return []


def _external_nutriments(data: dict[str, object]) -> dict[str, float]:
    values = data.get("nutriments")
    if isinstance(values, dict):
        return {
            key: value
            for key, value in values.items()
            if isinstance(key, str)
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        }
    return {}


barcode_lookup_service = BarcodeLookupService()

__all__ = [
    "OpenFoodFactsInvalidResponse",
    "OpenFoodFactsUnavailable",
    "barcode_lookup_service",
    "logger",
]
