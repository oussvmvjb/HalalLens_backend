import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.dependencies.auth import CurrentUser, DatabaseSession, OptionalUser
from app.models import Product, Scan, ScanStatus, ScanType, User
from app.repositories.scans import scan_by_id, user_scan_history
from app.schemas.api import (
    ScanAnalyzeRequest,
    ScanBarcodeRequest,
    ScanDetailsResponse,
    ScanHistoryItem,
    ScanResponse,
    ScanResultResponse,
)
from app.models import ScanResult
from app.services.scans import analyze_scan, build_snapshot
from app.services.barcode_lookup import barcode_lookup_service
from app.services.ingredient_processing import extract_ingredients

router = APIRouter(prefix="/scans", tags=["Scanning"])
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def image_signature_matches(content_type: str, content: bytes) -> bool:
    signatures = {
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": content.startswith(b"\xff\xd8\xff"),
        "image/gif": content.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP",
    }
    return signatures.get(content_type, False)


async def save_scan(db: AsyncSession, user: User | None, barcode: str | None) -> Scan:
    product = None
    if barcode is not None:
        product = await db.scalar(select(Product).where(Product.barcode == barcode).limit(1))
    scan = Scan(
        user_id=user.id if user else None,
        product_id=product.id if product else None,
        barcode=barcode,
        scan_type=ScanType.BARCODE if barcode else ScanType.IMAGE,
        scan_status=ScanStatus.RECEIVED,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    scan.snapshot = build_snapshot(barcode=barcode)
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    return scan


@router.post("/barcode", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def scan_barcode(
    payload: ScanBarcodeRequest, db: DatabaseSession, user: OptionalUser
) -> Scan:
    lookup = await barcode_lookup_service.lookup(db, payload.barcode, user)
    if lookup is None:
        raise HTTPException(status_code=404, detail="No product was found for this barcode.")
    statement = select(Scan).where(Scan.barcode == payload.barcode)
    if user is not None:
        statement = statement.where(Scan.user_id == user.id)
    else:
        statement = statement.where(Scan.user_id.is_(None))
    scan = await db.scalar(statement.order_by(Scan.created_at.desc()).limit(1))
    if scan is None:
        raise HTTPException(status_code=500, detail="Scan could not be saved")
    return scan


@router.post("/image", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def scan_image(
    db: DatabaseSession,
    user: OptionalUser,
    request: Request,
    image: Annotated[UploadFile, File()],
) -> Scan:
    if image.content_type is None or image.content_type not in {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Image required")
    content = await image.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Image exceeds 10 MiB")
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Image is empty")
    if not image_signature_matches(image.content_type, content):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image content does not match its declared format",
        )
    scan = await save_scan(db, user, None)
    scan.scan_status = ScanStatus.PROCESSING
    db.add(scan)
    await db.commit()
    try:
        selected_languages = ["en", "fr", "ar"]
        regions = await asyncio.to_thread(request.app.state.ocr_runtime.extract_text, content, selected_languages)
        ingredients, warnings = extract_ingredients(regions)
        scan.original_ingredients_text = "\n".join(region.text for region in regions) or None
        scan.detected_language = ",".join(selected_languages)
        analysis = await analyze_scan(
            db,
            scan,
            original_ingredients_text=scan.original_ingredients_text,
            detected_language=scan.detected_language,
        )
        scan.snapshot = build_snapshot(
            barcode=None,
            result=analysis,
            original_ingredients_text=scan.original_ingredients_text,
            ingredients=[
                {
                    "original_name": item.original_text,
                    "normalized_name": item.normalized_text,
                    "confidence_score": item.normalization_confidence,
                    "warning": "Needs review" if item.needs_review else None,
                }
                for item in ingredients
            ],
            warnings=warnings,
        )
        db.add(scan)
        await db.commit()
        await db.refresh(scan)
    except Exception:
        await db.rollback()
        scan = await db.get(Scan, scan.id)
        if scan is not None:
            scan.scan_status = ScanStatus.FAILED
            db.add(scan)
            await db.commit()
            await db.refresh(scan)
    return scan


@router.post("/analyze", response_model=ScanResultResponse)
async def analyze(
    payload: ScanAnalyzeRequest, db: DatabaseSession, user: OptionalUser
) -> ScanResult:
    scan = await scan_by_id(db, payload.scan_id)
    if scan is None or (scan.user_id is not None and (user is None or scan.user_id != user.id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    return await analyze_scan(
        db,
        scan,
        original_ingredients_text=payload.original_ingredients_text,
        detected_language=payload.detected_language,
    )


def _snapshot(scan: Scan) -> dict[str, object]:
    snapshot = scan.snapshot if isinstance(scan.snapshot, dict) else {}
    return snapshot if snapshot else build_snapshot(
        product=scan.product,
        barcode=scan.barcode,
        result=scan.results[-1] if scan.results else None,
        original_ingredients_text=scan.original_ingredients_text,
    )


def _history_item(scan: Scan) -> dict[str, object]:
    snapshot = _snapshot(scan)
    product = snapshot.get("product", {})
    analysis = snapshot.get("analysis", {})
    if not isinstance(product, dict):
        product = {}
    if not isinstance(analysis, dict):
        analysis = {}
    return {
        "id": scan.id,
        "user_id": scan.user_id,
        "product_id": scan.product_id,
        "scan_type": scan.scan_type,
        "barcode": scan.barcode,
        "scan_status": scan.scan_status,
        "created_at": scan.created_at,
        "product": product,
        "analysis": analysis,
    }


@router.get("/history", response_model=list[ScanHistoryItem])
async def history(
    db: DatabaseSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    scans = await user_scan_history(db, user.id, limit, offset)
    return [_history_item(scan) for scan in scans]


@router.get("/{scan_id}", response_model=ScanDetailsResponse)
async def get_scan(scan_id: UUID, db: DatabaseSession, user: CurrentUser) -> dict[str, object]:
    scan = await scan_by_id(db, scan_id)
    if scan is None or scan.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    item = _history_item(scan)
    item.update({
        "image_url": scan.image_url,
        "original_ingredients_text": scan.original_ingredients_text,
        "detected_language": scan.detected_language,
        "analysis_completed_at": scan.analysis_completed_at,
    })
    return item


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan(scan_id: UUID, db: DatabaseSession, user: CurrentUser) -> None:
    scan = await scan_by_id(db, scan_id)
    if scan is None or scan.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    await db.delete(scan)
    await db.commit()
