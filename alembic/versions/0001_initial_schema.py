"""Create the initial HalalLens schema.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

from app.db.migration_helpers import ensure_postgres_enum, index_exists, table_exists

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_ENUMS: dict[str, tuple[str, ...]] = {
    "authority_status": ("RECOGNIZED", "UNRECOGNIZED", "PENDING", "REVOKED"),
    "risk_category": ("LOW", "MEDIUM", "HIGH", "SOURCE_DEPENDENT", "UNKNOWN"),
    "verification_status": ("VERIFIED", "UNVERIFIED", "REJECTED", "EXPIRED", "PENDING"),
    "scan_status": ("RECEIVED", "PROCESSING", "COMPLETED", "FAILED"),
    "halal_status": ("HALAL_VERIFIED", "HARAM_FLAG", "DOUBTFUL", "UNKNOWN"),
    "report_type": ("INCORRECT_INGREDIENT", "CERTIFICATION", "PRODUCT_DATA", "OTHER"),
    "report_status": ("OPEN", "UNDER_REVIEW", "RESOLVED", "REJECTED"),
    "evidence_type": ("INGREDIENT", "CERTIFICATION", "SOURCE", "USER_REPORT", "OTHER"),
    "evidence_verification_status": ("VERIFIED", "UNVERIFIED", "REJECTED", "EXPIRED", "PENDING"),
}


def upgrade() -> None:
    bind = op.get_bind()
    for enum_name, enum_values in _ENUMS.items():
        ensure_postgres_enum(bind, enum_name, enum_values)

    if not table_exists(bind, "certification_issuers"):
        op.create_table(
            "certification_issuers",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("country", sa.String(length=2), nullable=True),
            sa.Column("website", sa.String(length=2048), nullable=True),
            sa.Column(
                "authority_status",
                postgresql.ENUM(*_ENUMS["authority_status"], name="authority_status", create_type=False),
                nullable=False,
            ),
            sa.Column("verification_notes", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_certification_issuers")),
        )
    if not index_exists(bind, "certification_issuers", op.f("ix_certification_issuers_name")):
        op.create_index(
            op.f("ix_certification_issuers_name"),
            "certification_issuers",
            ["name"],
            unique=False,
        )

    if not table_exists(bind, "ingredients"):
        op.create_table(
            "ingredients",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("canonical_name", sa.String(length=255), nullable=False),
            sa.Column("normalized_name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("ingredient_code", sa.String(length=64), nullable=True),
            sa.Column(
                "risk_category",
                postgresql.ENUM(*_ENUMS["risk_category"], name="risk_category", create_type=False),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_ingredients")),
        )
    if not index_exists(bind, "ingredients", op.f("ix_ingredients_canonical_name")):
        op.create_index(op.f("ix_ingredients_canonical_name"), "ingredients", ["canonical_name"])
    if not index_exists(bind, "ingredients", op.f("ix_ingredients_ingredient_code")):
        op.create_index(op.f("ix_ingredients_ingredient_code"), "ingredients", ["ingredient_code"])
    if not index_exists(bind, "ingredients", op.f("ix_ingredients_normalized_name")):
        op.create_index(op.f("ix_ingredients_normalized_name"), "ingredients", ["normalized_name"])

    if not table_exists(bind, "products"):
        op.create_table(
            "products",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("barcode", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("brand", sa.String(length=160), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("image_url", sa.String(length=2048), nullable=True),
            sa.Column("country_of_origin", sa.String(length=2), nullable=True),
            sa.Column("source", sa.String(length=120), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        )
    if not index_exists(bind, "products", op.f("ix_products_barcode")) and not index_exists(
        bind, "products", "uq_products_barcode"
    ):
        op.create_index(op.f("ix_products_barcode"), "products", ["barcode"])

    if not table_exists(bind, "users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("full_name", sa.String(length=160), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("hashed_password", sa.String(length=255), nullable=False),
            sa.Column("preferred_language", sa.String(length=5), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        )
    if not index_exists(bind, "users", op.f("ix_users_email")):
        op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    if not table_exists(bind, "halal_certifications"):
        op.create_table(
            "halal_certifications",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("product_id", sa.Uuid(), nullable=False),
            sa.Column("issuer_id", sa.Uuid(), nullable=False),
            sa.Column("certificate_number", sa.String(length=120), nullable=True),
            sa.Column("issue_date", sa.Date(), nullable=True),
            sa.Column("expiry_date", sa.Date(), nullable=True),
            sa.Column(
                "verification_status",
                postgresql.ENUM(
                    *_ENUMS["verification_status"], name="verification_status", create_type=False
                ),
                nullable=False,
            ),
            sa.Column("evidence_url", sa.String(length=2048), nullable=True),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["issuer_id"],
                ["certification_issuers.id"],
                name=op.f("fk_halal_certifications_issuer_id_certification_issuers"),
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["product_id"],
                ["products.id"],
                name=op.f("fk_halal_certifications_product_id_products"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_halal_certifications")),
        )
    if not index_exists(bind, "halal_certifications", "ix_halal_certifications_product_status"):
        op.create_index(
            "ix_halal_certifications_product_status",
            "halal_certifications",
            ["product_id", "verification_status"],
        )

    if not table_exists(bind, "ingredient_translations"):
        op.create_table(
            "ingredient_translations",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("ingredient_id", sa.Uuid(), nullable=False),
            sa.Column("language_code", sa.String(length=5), nullable=False),
            sa.Column("translated_name", sa.String(length=255), nullable=False),
            sa.Column(
                "aliases",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["ingredient_id"],
                ["ingredients.id"],
                name=op.f("fk_ingredient_translations_ingredient_id_ingredients"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_ingredient_translations")),
            sa.UniqueConstraint(
                "ingredient_id",
                "language_code",
                name=op.f("uq_ingredient_translations_ingredient_id"),
            ),
        )
    if not index_exists(bind, "ingredient_translations", "ix_ingredient_translations_language_code"):
        op.create_index(
            "ix_ingredient_translations_language_code",
            "ingredient_translations",
            ["language_code"],
        )

    if not table_exists(bind, "product_ingredients"):
        op.create_table(
            "product_ingredients",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("product_id", sa.Uuid(), nullable=False),
            sa.Column("ingredient_id", sa.Uuid(), nullable=False),
            sa.Column("original_name", sa.String(length=255), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("source_language", sa.String(length=5), nullable=True),
            sa.Column("source_verified", sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(
                ["ingredient_id"],
                ["ingredients.id"],
                name=op.f("fk_product_ingredients_ingredient_id_ingredients"),
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["product_id"],
                ["products.id"],
                name=op.f("fk_product_ingredients_product_id_products"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_product_ingredients")),
            sa.UniqueConstraint(
                "product_id",
                "position",
                name=op.f("uq_product_ingredients_product_id"),
            ),
        )
    if not index_exists(bind, "product_ingredients", "ix_product_ingredients_ingredient_id"):
        op.create_index(
            "ix_product_ingredients_ingredient_id",
            "product_ingredients",
            ["ingredient_id"],
        )

    if not table_exists(bind, "scans"):
        op.create_table(
            "scans",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=True),
            sa.Column("product_id", sa.Uuid(), nullable=True),
            sa.Column("barcode", sa.String(length=64), nullable=True),
            sa.Column("image_url", sa.String(length=2048), nullable=True),
            sa.Column("original_ingredients_text", sa.Text(), nullable=True),
            sa.Column("detected_language", sa.String(length=5), nullable=True),
            sa.Column(
                "scan_status",
                postgresql.ENUM(*_ENUMS["scan_status"], name="scan_status", create_type=False),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["product_id"],
                ["products.id"],
                name=op.f("fk_scans_product_id_products"),
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f("fk_scans_user_id_users"),
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_scans")),
        )
    if not index_exists(bind, "scans", "ix_scans_barcode"):
        op.create_index("ix_scans_barcode", "scans", ["barcode"])
    if not index_exists(bind, "scans", "ix_scans_user_created_at"):
        op.create_index("ix_scans_user_created_at", "scans", ["user_id", "created_at"])

    if not table_exists(bind, "scan_results"):
        op.create_table(
            "scan_results",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("scan_id", sa.Uuid(), nullable=False),
            sa.Column(
                "overall_status",
                postgresql.ENUM(*_ENUMS["halal_status"], name="halal_status", create_type=False),
                nullable=False,
            ),
            sa.Column("confidence_score", sa.Float(), nullable=True),
            sa.Column("explanation", sa.Text(), nullable=False),
            sa.Column("analysis_version", sa.String(length=64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
                name=op.f("ck_scan_results_confidence_score_range"),
            ),
            sa.ForeignKeyConstraint(
                ["scan_id"],
                ["scans.id"],
                name=op.f("fk_scan_results_scan_id_scans"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_results")),
        )
    if not index_exists(bind, "scan_results", "ix_scan_results_scan_created_at"):
        op.create_index("ix_scan_results_scan_created_at", "scan_results", ["scan_id", "created_at"])

    if not table_exists(bind, "user_reports"):
        op.create_table(
            "user_reports",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("product_id", sa.Uuid(), nullable=True),
            sa.Column("scan_id", sa.Uuid(), nullable=True),
            sa.Column(
                "report_type",
                postgresql.ENUM(*_ENUMS["report_type"], name="report_type", create_type=False),
                nullable=False,
            ),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(*_ENUMS["report_status"], name="report_status", create_type=False),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["product_id"],
                ["products.id"],
                name=op.f("fk_user_reports_product_id_products"),
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["scan_id"],
                ["scans.id"],
                name=op.f("fk_user_reports_scan_id_scans"),
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f("fk_user_reports_user_id_users"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_user_reports")),
        )
    if not index_exists(bind, "user_reports", "ix_user_reports_user_created_at"):
        op.create_index("ix_user_reports_user_created_at", "user_reports", ["user_id", "created_at"])

    if not table_exists(bind, "scan_evidence"):
        op.create_table(
            "scan_evidence",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("scan_result_id", sa.Uuid(), nullable=False),
            sa.Column("ingredient_id", sa.Uuid(), nullable=True),
            sa.Column(
                "evidence_type",
                postgresql.ENUM(*_ENUMS["evidence_type"], name="evidence_type", create_type=False),
                nullable=False,
            ),
            sa.Column("evidence_description", sa.Text(), nullable=False),
            sa.Column("source_name", sa.String(length=255), nullable=True),
            sa.Column("source_url", sa.String(length=2048), nullable=True),
            sa.Column(
                "verification_status",
                postgresql.ENUM(
                    *_ENUMS["evidence_verification_status"],
                    name="evidence_verification_status",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["ingredient_id"],
                ["ingredients.id"],
                name=op.f("fk_scan_evidence_ingredient_id_ingredients"),
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["scan_result_id"],
                ["scan_results.id"],
                name=op.f("fk_scan_evidence_scan_result_id_scan_results"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_evidence")),
        )


def downgrade() -> None:
    op.drop_table("scan_evidence")
    op.drop_index("ix_user_reports_user_created_at", table_name="user_reports")
    op.drop_table("user_reports")
    op.drop_index("ix_scan_results_scan_created_at", table_name="scan_results")
    op.drop_table("scan_results")
    op.drop_index("ix_scans_user_created_at", table_name="scans")
    op.drop_index("ix_scans_barcode", table_name="scans")
    op.drop_table("scans")
    op.drop_index("ix_product_ingredients_ingredient_id", table_name="product_ingredients")
    op.drop_table("product_ingredients")
    op.drop_index("ix_ingredient_translations_language_code", table_name="ingredient_translations")
    op.drop_table("ingredient_translations")
    op.drop_index("ix_halal_certifications_product_status", table_name="halal_certifications")
    op.drop_table("halal_certifications")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_products_barcode"), table_name="products")
    op.drop_table("products")
    op.drop_index(op.f("ix_ingredients_normalized_name"), table_name="ingredients")
    op.drop_index(op.f("ix_ingredients_ingredient_code"), table_name="ingredients")
    op.drop_index(op.f("ix_ingredients_canonical_name"), table_name="ingredients")
    op.drop_table("ingredients")
    op.drop_index(op.f("ix_certification_issuers_name"), table_name="certification_issuers")
    op.drop_table("certification_issuers")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for type_name in (
            "evidence_verification_status",
            "evidence_type",
            "report_status",
            "report_type",
            "halal_status",
            "scan_status",
            "verification_status",
            "risk_category",
            "authority_status",
        ):
            postgresql.ENUM(name=type_name).drop(bind, checkfirst=True)
