from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.dependencies.auth import DatabaseSession
from app.models import CertificationIssuer, HalalCertification
from app.schemas.api import CertificationResponse, IssuerResponse

router = APIRouter(prefix="/certifications", tags=["Certifications"])


@router.get("/", response_model=list[CertificationResponse])
async def list_certifications(
    db: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[HalalCertification]:
    result = await db.scalars(
        select(HalalCertification).order_by(HalalCertification.id).offset(offset).limit(limit)
    )
    return list(result.all())


@router.get("/issuers", response_model=list[IssuerResponse])
async def list_issuers(
    db: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CertificationIssuer]:
    result = await db.scalars(
        select(CertificationIssuer)
        .order_by(CertificationIssuer.name)
        .offset(offset)
        .limit(limit)
    )
    return list(result.all())


@router.get("/product/{product_id}", response_model=list[CertificationResponse])
async def certifications_for_product(
    product_id: UUID, db: DatabaseSession
) -> list[HalalCertification]:
    result = await db.scalars(
        select(HalalCertification)
        .where(HalalCertification.product_id == product_id)
        .order_by(HalalCertification.expiry_date.desc())
    )
    return list(result.all())
