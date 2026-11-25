"""Store Open Food Facts metadata and enforce unique product barcodes.

Revision ID: 0002_barcode_product_data
Revises: 0001_initial
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.migration_helpers import column_exists, index_exists

revision = "0002_barcode_product_data"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    if not index_exists(bind, "products", "uq_products_barcode"):
        duplicates = bind.execute(
            sa.text(
                "SELECT barcode FROM products "
                "GROUP BY barcode HAVING COUNT(*) > 1 LIMIT 10"
            )
        ).scalars().all()
        if duplicates:
            raise RuntimeError(
                "Cannot enforce unique product barcodes: duplicate values exist. "
                "Resolve these records explicitly before retrying the migration."
            )

    if not column_exists(bind, "products", "external_data"):
        op.add_column(
            "products",
            sa.Column(
                "external_data",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
        )

    if index_exists(bind, "products", "ix_products_barcode"):
        op.drop_index("ix_products_barcode", table_name="products")
    if not index_exists(bind, "products", "uq_products_barcode"):
        op.create_index("uq_products_barcode", "products", ["barcode"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_products_barcode", table_name="products")
    op.create_index("ix_products_barcode", "products", ["barcode"], unique=False)
    op.drop_column("products", "external_data")
