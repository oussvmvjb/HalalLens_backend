from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.dependencies.auth import CurrentUser, DatabaseSession, OptionalUser
from app.models import Product, Scan, ScanStatus, User
from app.repositories.scans import scan_by_id, user_scan_history
from app.schemas.api import (
    ScanAnalyzeRequest,
    ScanBarcodeRequest,
    ScanResponse,
    ScanResultResponse,
)
from app.models import ScanResult
from app.services.scans import analyze_scan

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
        scan_status=ScanStatus.RECEIVED,
    )
    db.add(scan)
    await db.commit()
    await db.refresh(scan)
    return scan


@router.post("/barcode", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def scan_barcode(
    payload: ScanBarcodeRequest, db: DatabaseSession, user: OptionalUser
) -> Scan:
    return await save_scan(db, user, payload.barcode)


@router.post("/image", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def scan_image(
    db: DatabaseSession,
    user: OptionalUser,
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
    # File storage and OCR are intentionally deferred; do not retain uploaded image bytes.
    return await save_scan(db, user, None)


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


@router.get("/history", response_model=list[ScanResponse])
async def history(
    db: DatabaseSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Scan]:
    return await user_scan_history(db, user.id, limit, offset)


@router.get("/{scan_id}", response_model=ScanResponse)
async def get_scan(scan_id: UUID, db: DatabaseSession, user: OptionalUser) -> Scan:
    scan = await scan_by_id(db, scan_id)
    if scan is None or (scan.user_id is not None and (user is None or scan.user_id != user.id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    return scan
