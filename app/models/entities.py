from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base
from app.models.enums import (
    AuthorityStatus,
    EvidenceType,
    HalalStatus,
    ReportStatus,
    ReportType,
    RiskCategory,
    ScanStatus,
    ScanType,
    VerificationStatus,
)

UUID_TYPE = Uuid(as_uuid=True)
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(5), default="en", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    scans: Mapped[list["Scan"]] = relationship(back_populates="user", passive_deletes=True)
    reports: Mapped[list["UserReport"]] = relationship(back_populates="user", passive_deletes=True)


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (Index("uq_products_barcode", "barcode", unique=True),)

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(2048))
    country_of_origin: Mapped[str | None] = mapped_column(String(2))
    source: Mapped[str | None] = mapped_column(String(120))
    external_data: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )

    ingredients: Mapped[list["ProductIngredient"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    certifications: Mapped[list["HalalCertification"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    scans: Mapped[list["Scan"]] = relationship(back_populates="product")


class ProductDetailsTranslationCache(CreatedAtMixin, Base):
    __tablename__ = "product_details_translation_cache"

    source_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_language: Mapped[str] = mapped_column(String(5), primary_key=True)
    translated_details: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE,
        nullable=False,
    )


class Ingredient(TimestampMixin, Base):
    __tablename__ = "ingredients"

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    ingredient_code: Mapped[str | None] = mapped_column(String(64), index=True)
    risk_category: Mapped[RiskCategory] = mapped_column(
        Enum(RiskCategory, name="risk_category"), default=RiskCategory.UNKNOWN, nullable=False
    )

    translations: Mapped[list["IngredientTranslation"]] = relationship(
        back_populates="ingredient", cascade="all, delete-orphan"
    )
    product_links: Mapped[list["ProductIngredient"]] = relationship(back_populates="ingredient")
    evidence: Mapped[list["ScanEvidence"]] = relationship(back_populates="ingredient")


class IngredientTranslation(Base):
    __tablename__ = "ingredient_translations"
    __table_args__ = (
        UniqueConstraint("ingredient_id", "language_code"),
        Index("ix_ingredient_translations_language_code", "language_code"),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    ingredient_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False
    )
    language_code: Mapped[str] = mapped_column(String(5), nullable=False)
    translated_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON_TYPE, default=list, nullable=False)
    ingredient: Mapped[Ingredient] = relationship(back_populates="translations")


class ProductIngredient(Base):
    __tablename__ = "product_ingredients"
    __table_args__ = (
        UniqueConstraint("product_id", "position"),
        Index("ix_product_ingredients_ingredient_id", "ingredient_id"),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_language: Mapped[str | None] = mapped_column(String(5))
    source_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    product: Mapped[Product] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient] = relationship(back_populates="product_links")


class CertificationIssuer(Base):
    __tablename__ = "certification_issuers"

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    country: Mapped[str | None] = mapped_column(String(2))
    website: Mapped[str | None] = mapped_column(String(2048))
    authority_status: Mapped[AuthorityStatus] = mapped_column(
        Enum(AuthorityStatus, name="authority_status"),
        default=AuthorityStatus.PENDING,
        nullable=False,
    )
    verification_notes: Mapped[str | None] = mapped_column(Text)
    certifications: Mapped[list["HalalCertification"]] = relationship(back_populates="issuer")


class HalalCertification(Base):
    __tablename__ = "halal_certifications"
    __table_args__ = (
        Index("ix_halal_certifications_product_status", "product_id", "verification_status"),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    issuer_id: Mapped[UUID] = mapped_column(
        ForeignKey("certification_issuers.id", ondelete="RESTRICT"), nullable=False
    )
    certificate_number: Mapped[str | None] = mapped_column(String(120))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status"),
        default=VerificationStatus.PENDING,
        nullable=False,
    )
    evidence_url: Mapped[str | None] = mapped_column(String(2048))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    product: Mapped[Product] = relationship(back_populates="certifications")
    issuer: Mapped[CertificationIssuer] = relationship(back_populates="certifications")


class Scan(CreatedAtMixin, Base):
    __tablename__ = "scans"
    __table_args__ = (
        Index("ix_scans_user_created_at", "user_id", "created_at"),
        Index("ix_scans_barcode", "barcode"),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    barcode: Mapped[str | None] = mapped_column(String(64))
    image_url: Mapped[str | None] = mapped_column(String(2048))
    original_ingredients_text: Mapped[str | None] = mapped_column(Text)
    detected_language: Mapped[str | None] = mapped_column(String(5))
    scan_type: Mapped[ScanType] = mapped_column(
        Enum(ScanType, name="scan_type"), default=ScanType.BARCODE, nullable=False
    )
    scan_status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus, name="scan_status"), default=ScanStatus.RECEIVED, nullable=False
    )
    analysis_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON_TYPE, default=dict, nullable=False
    )
    user: Mapped[User | None] = relationship(back_populates="scans")
    product: Mapped[Product | None] = relationship(back_populates="scans")
    results: Mapped[list["ScanResult"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )
    reports: Mapped[list["UserReport"]] = relationship(back_populates="scan")


class ScanResult(Base):
    __tablename__ = "scan_results"
    __table_args__ = (
        Index("ix_scan_results_scan_created_at", "scan_id", "created_at"),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="confidence_score_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    scan_id: Mapped[UUID] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), nullable=False)
    overall_status: Mapped[HalalStatus] = mapped_column(
        Enum(HalalStatus, name="halal_status"), default=HalalStatus.UNKNOWN, nullable=False
    )
    confidence_score: Mapped[float | None] = mapped_column()
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(64), default="foundation-v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    scan: Mapped[Scan] = relationship(back_populates="results")
    evidence: Mapped[list["ScanEvidence"]] = relationship(
        back_populates="scan_result", cascade="all, delete-orphan"
    )


class ScanEvidence(Base):
    __tablename__ = "scan_evidence"

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    scan_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("scan_results.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ingredients.id", ondelete="SET NULL")
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(
        Enum(EvidenceType, name="evidence_type"), nullable=False
    )
    evidence_description: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="evidence_verification_status"),
        default=VerificationStatus.UNVERIFIED,
        nullable=False,
    )
    scan_result: Mapped[ScanResult] = relationship(back_populates="evidence")
    ingredient: Mapped[Ingredient | None] = relationship(back_populates="evidence")


class UserReport(Base):
    __tablename__ = "user_reports"
    __table_args__ = (Index("ix_user_reports_user_created_at", "user_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    scan_id: Mapped[UUID | None] = mapped_column(ForeignKey("scans.id", ondelete="SET NULL"))
    report_type: Mapped[ReportType] = mapped_column(
        Enum(ReportType, name="report_type"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"), default=ReportStatus.OPEN, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    user: Mapped[User] = relationship(back_populates="reports")
    scan: Mapped[Scan | None] = relationship(back_populates="reports")
