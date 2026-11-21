from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Scan, UserReport


async def scan_by_id(db: AsyncSession, scan_id: UUID) -> Scan | None:
    return await db.get(Scan, scan_id)


async def user_scan_history(
    db: AsyncSession, user_id: UUID, limit: int, offset: int
) -> list[Scan]:
    result = await db.scalars(
        select(Scan)
        .where(Scan.user_id == user_id)
        .order_by(Scan.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.all())


async def user_report_history(
    db: AsyncSession, user_id: UUID, limit: int, offset: int
) -> list[UserReport]:
    result = await db.scalars(
        select(UserReport)
        .where(UserReport.user_id == user_id)
        .order_by(UserReport.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.all())
