from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.models.enums import (
    AuthorityStatus,
    HalalStatus,
    ProductHalalVerdict,
    VerificationStatus,
)
from app.schemas.halal import IngredientClassificationResponse
from app.services.barcodes import is_valid_gtin

GTIN = Annotated[str, StringConstraints(pattern=r"^(?:\d{8}|\d{12,14})$")]


class BarcodeLookupRequest(BaseModel):
    barcode: GTIN

    @field_validator("barcode")
    @classmethod
    def barcode_must_have_valid_check_digit(cls, value: str) -> str:
        if not is_valid_gtin(value):
            raise ValueError("Barcode format or check digit is invalid.")
        return value


class BarcodeProductResponse(BaseModel):
    id: UUID
    barcode: str
    name: str | None
    brand: str | None
    image_url: str | None
    source: str | None
    ingredients: list[str] = Field(default_factory=list)
    ingredients_text: str | None = None
    categories: list[str] = Field(default_factory=list)
    country_of_origin: str | None = None
    origin_description: str | None = None
    labels: list[str] = Field(default_factory=list)
    nutriments: dict[str, float] = Field(default_factory=dict)


class BarcodeCertificationResponse(BaseModel):
    issuer: str
    issuer_authority_status: AuthorityStatus
    certificate_number: str | None
    expiry_date: date | None
    verification_status: VerificationStatus
    evidence_url: str | None


class BarcodeAnalysisResponse(BaseModel):
    scan_id: UUID
    overall_status: HalalStatus
    verdict: ProductHalalVerdict = ProductHalalVerdict.INSUFFICIENT_INFORMATION
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    explanation: str
    analysis_version: str = "halal-engine-v1"
    analyzed_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    ingredients_analysis: list[IngredientClassificationResponse] = Field(default_factory=list)


class BarcodeLookupResponse(BaseModel):
    success: Literal[True] = True
    status: Literal["completed"] = "completed"
    message: str = "Product lookup completed."
    product: BarcodeProductResponse
    analysis: BarcodeAnalysisResponse
    certifications: list[BarcodeCertificationResponse] = Field(default_factory=list)


class BarcodeLookupFailure(BaseModel):
    success: Literal[False] = False
    status: Literal["failed"] = "failed"
    error_code: str
    message: str
    retryable: bool
