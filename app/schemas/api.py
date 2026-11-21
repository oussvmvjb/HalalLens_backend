from typing import Annotated
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, StringConstraints, field_validator

from app.models.enums import (
    AuthorityStatus,
    HalalStatus,
    ReportStatus,
    ReportType,
    RiskCategory,
    ScanStatus,
    VerificationStatus,
)

Barcode = Annotated[str, StringConstraints(min_length=4, max_length=64, pattern=r"^[0-9A-Za-z-]+$")]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    preferred_language: str = Field(default="en", min_length=2, max_length=5)

    @field_validator("full_name")
    @classmethod
    def full_name_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name cannot be blank")
        return normalized


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(ORMModel):
    id: UUID
    full_name: str
    email: EmailStr
    preferred_language: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ProductCreate(BaseModel):
    barcode: Barcode
    name: str = Field(min_length=1, max_length=255)
    brand: str | None = Field(default=None, max_length=160)
    description: str | None = None
    image_url: HttpUrl | None = None
    country_of_origin: str | None = Field(
        default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$"
    )
    source: str | None = Field(default=None, max_length=120)


class ProductResponse(ORMModel):
    id: UUID
    barcode: str
    name: str
    brand: str | None
    description: str | None
    image_url: str | None
    country_of_origin: str | None
    source: str | None
    created_at: datetime


class IngredientResponse(ORMModel):
    id: UUID
    canonical_name: str
    normalized_name: str
    description: str | None
    ingredient_code: str | None
    risk_category: RiskCategory


class CertificationResponse(ORMModel):
    id: UUID
    product_id: UUID
    issuer_id: UUID
    certificate_number: str | None
    issue_date: date | None
    expiry_date: date | None
    verification_status: VerificationStatus
    evidence_url: str | None
    last_verified_at: datetime | None


class IssuerResponse(ORMModel):
    id: UUID
    name: str
    country: str | None
    website: str | None
    authority_status: AuthorityStatus
    verification_notes: str | None


class ScanBarcodeRequest(BaseModel):
    barcode: Barcode


class ScanAnalyzeRequest(BaseModel):
    scan_id: UUID
    original_ingredients_text: str | None = Field(default=None, max_length=20000)
    detected_language: str | None = Field(default=None, min_length=2, max_length=5)


class ScanResultResponse(ORMModel):
    id: UUID
    overall_status: HalalStatus
    confidence_score: float | None = Field(ge=0, le=1)
    explanation: str
    analysis_version: str
    created_at: datetime


class ScanResponse(ORMModel):
    id: UUID
    user_id: UUID | None
    product_id: UUID | None
    barcode: str | None
    image_url: str | None
    original_ingredients_text: str | None
    detected_language: str | None
    scan_status: ScanStatus
    created_at: datetime


class ReportCreate(BaseModel):
    product_id: UUID | None = None
    scan_id: UUID | None = None
    report_type: ReportType
    description: str = Field(min_length=5, max_length=5000)


class ReportResponse(ORMModel):
    id: UUID
    user_id: UUID
    product_id: UUID | None
    scan_id: UUID | None
    report_type: ReportType
    description: str
    status: ReportStatus
    created_at: datetime
