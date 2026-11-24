"""Add complete immutable scan history snapshots.

Revision ID: 0004_scan_history
Revises: 0003_product_translation_cache
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_scan_history"
down_revision = "0003_product_translation_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    scan_type = sa.Enum("BARCODE", "IMAGE", name="scan_type")
    scan_type.create(op.get_bind(), checkfirst=True)
    op.add_column("scans", sa.Column("scan_type", scan_type, server_default=sa.text("'BARCODE'"), nullable=False))
    op.add_column("scans", sa.Column("analysis_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "scans",
        sa.Column(
            "snapshot",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_index("ix_scans_product_id", "scans", ["product_id"], unique=False)
    op.create_index("ix_scans_scan_status", "scans", ["scan_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_scans_scan_status", table_name="scans")
    op.drop_index("ix_scans_product_id", table_name="scans")
    op.drop_column("scans", "snapshot")
    op.drop_column("scans", "analysis_completed_at")
    op.drop_column("scans", "scan_type")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(name="scan_type").drop(bind, checkfirst=True)
