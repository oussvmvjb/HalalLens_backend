from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from app.api.dependencies.auth import CurrentUser, DatabaseSession
from app.models import Product, Scan, UserReport
from app.repositories.scans import user_report_history
from app.schemas.api import ReportCreate, ReportResponse

router = APIRouter(prefix="/reports", tags=["User reports"])


@router.post("/", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate, db: DatabaseSession, user: CurrentUser
) -> UserReport:
    if payload.product_id is not None and await db.get(Product, payload.product_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    if payload.scan_id is not None:
        scan = await db.get(Scan, payload.scan_id)
        if scan is None or scan.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    report = UserReport(user_id=user.id, **payload.model_dump())
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


@router.get("/history", response_model=list[ReportResponse])
async def report_history(
    db: DatabaseSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UserReport]:
    return await user_report_history(db, user.id, limit, offset)
