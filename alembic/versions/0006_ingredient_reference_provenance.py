"""Add source_classification, imported_at, and pending_review provenance fields.

Revision ID: 0006_reference_provenance
Revises: 0005_halal_knowledge_base
"""
import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import column_exists, ensure_postgres_enum, index_exists

revision = "0006_reference_provenance"
down_revision = "0005_halal_knowledge_base"
branch_labels = None
depends_on = None

_EVIDENCE_REVIEW_STATUS = ("verified", "pending", "pending_review", "disputed", "rejected")


def upgrade() -> None:
    bind = op.get_bind()
    ensure_postgres_enum(bind, "evidence_review_status", _EVIDENCE_REVIEW_STATUS)

    if not column_exists(bind, "ingredients", "source_classification"):
        op.add_column(
            "ingredients",
            sa.Column("source_classification", sa.String(length=64), nullable=True),
        )
    if not index_exists(bind, "ingredients", op.f("ix_ingredients_source_classification")):
        op.create_index(
            op.f("ix_ingredients_source_classification"),
            "ingredients",
            ["source_classification"],
            unique=False,
        )

    if not column_exists(bind, "ingredient_sources", "imported_at"):
        op.add_column(
            "ingredient_sources",
            sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("ingredient_sources", "imported_at")
    op.drop_index(op.f("ix_ingredients_source_classification"), table_name="ingredients")
    op.drop_column("ingredients", "source_classification")
