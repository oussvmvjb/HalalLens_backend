"""Cache product detail translations separately from source product data.

Revision ID: 0003_product_translation_cache
Revises: 0002_barcode_product_data
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.migration_helpers import table_exists

revision = "0003_product_translation_cache"
down_revision = "0002_barcode_product_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not table_exists(bind, "product_details_translation_cache"):
        op.create_table(
            "product_details_translation_cache",
            sa.Column("source_hash", sa.String(length=64), nullable=False),
            sa.Column("target_language", sa.String(length=5), nullable=False),
            sa.Column(
                "translated_details",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint(
                "source_hash",
                "target_language",
                name=op.f("pk_product_details_translation_cache"),
            ),
        )


def downgrade() -> None:
    op.drop_table("product_details_translation_cache")
