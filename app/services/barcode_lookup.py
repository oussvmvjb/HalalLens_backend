import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuthorityStatus,
    CertificationIssuer,
    HalalCertification,
    Product,
    ProductIngredient,
    Scan,
    ScanStatus,
    ScanType,
    User,
    VerificationStatus,
)
from app.schemas.barcode import (
    BarcodeAnalysisResponse,
    BarcodeCertificationResponse,
    BarcodeLookupResponse,
    BarcodeProductResponse,
)
from app.schemas.halal import (
    EvidenceReferenceResponse,
    IngredientClassificationResponse,
)
from app.services.halal_engine import CertificationEvidenceInput
from app.services.open_food_facts import (
    OpenFoodFactsInvalidResponse,
    OpenFoodFactsProduct,
    OpenFoodFactsUnavailable,
    fetch_open_food_facts_product,
)
from app.services.scans import analyze_scan, build_snapshot

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
        certifications = await _certifications_for_product(db, product.id)
        today = date.today()
        cert_inputs = [
            CertificationEvidenceInput(
                issuer=cert.issuer,
                is_recognized_issuer=cert.issuer_authority_status == AuthorityStatus.RECOGNIZED,
                is_verified=cert.verification_status == VerificationStatus.VERIFIED,
                is_expired=cert.expiry_date is not None and cert.expiry_date < today,
                certificate_number=cert.certificate_number,
            )
            for cert in certifications
        ]
        scan = Scan(
            user_id=user_id,
            product_id=product.id,
            barcode=barcode,
            scan_type=ScanType.BARCODE,
            scan_status=ScanStatus.RECEIVED,
        )
        db.add(scan)
        await db.flush()
        analysis, assessment = await analyze_scan(
            db,
            scan,
            original_ingredients_text=(
                product.external_data.get("ingredients_text")
                if isinstance(product.external_data.get("ingredients_text"), str)
                else ", ".join(ingredients) or None
            ),
            detected_language=None,
            ingredients_list=ingredients,
            certifications=cert_inputs,
            return_assessment=True,
        )
        scan.snapshot = build_snapshot(
            product=product,
            barcode=barcode,
            result=analysis,
            assessment=assessment,
            original_ingredients_text=(
                product.external_data.get("ingredients_text")
                if isinstance(product.external_data.get("ingredients_text"), str)
                else ", ".join(ingredients) or None
            ),
            ingredients=[{"original_name": name, "normalized_name": name} for name in ingredients],
            certifications=[
                {
                    "issuer": certification.issuer,
                    "issuer_authority_status": certification.issuer_authority_status.value,
                    "certificate_number": certification.certificate_number,
                    "expiry_date": certification.expiry_date.isoformat() if certification.expiry_date else None,
                    "verification_status": certification.verification_status.value,
                    "evidence_url": certification.evidence_url,
                }
                for certification in certifications
            ],
        )
        db.add(scan)
        await db.commit()
        await db.refresh(scan)
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
                verdict=assessment.verdict,
                confidence_score=analysis.confidence_score,
                explanation=analysis.explanation,
                analysis_version=assessment.analysis_version,
                analyzed_at=assessment.analyzed_at,
                warnings=list(assessment.warnings),
                ingredients_analysis=[
                    IngredientClassificationResponse(
                        original_text=item.normalized.original_text,
                        normalized_name=item.normalized.normalized_name,
                        e_number=item.normalized.e_number,
                        ins_number=item.normalized.ins_number,
                        status=item.status,
                        reason=item.reason,
                        match_type=item.match_type,
                        matched_record_id=item.record.id if item.record is not None else None,
                        canonical_name=item.record.canonical_name if item.record is not None else None,
                        confidence=item.confidence,
                        warnings=list(item.warnings),
                        evidence=[
                            EvidenceReferenceResponse(
                                source_name=ref.source_name,
                                source_url=ref.source_url,
                                evidence_summary=ref.evidence_summary,
                                evidence_type=ref.evidence_type,
                                reviewer_or_organization=ref.reviewer_or_organization,
                                verification_date=ref.verification_date,
                                review_status=ref.review_status,
                            )
                            for ref in item.evidence
                        ],
                    )
                    for item in assessment.classifications
                ],
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
