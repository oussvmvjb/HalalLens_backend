"""Add complete immutable scan history snapshots.

Revision ID: 0004_scan_history
Revises: 0003_product_translation_cache
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.migration_helpers import column_exists, ensure_postgres_enum, index_exists

revision = "0004_scan_history"
down_revision = "0003_product_translation_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_postgres_enum(bind, "scan_type", ("BARCODE", "IMAGE"))
    scan_type = postgresql.ENUM("BARCODE", "IMAGE", name="scan_type", create_type=False)

    if not column_exists(bind, "scans", "scan_type"):
        op.add_column(
            "scans",
            sa.Column("scan_type", scan_type, server_default=sa.text("'BARCODE'"), nullable=False),
        )
    if not column_exists(bind, "scans", "analysis_completed_at"):
        op.add_column(
            "scans",
            sa.Column("analysis_completed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not column_exists(bind, "scans", "snapshot"):
        op.add_column(
            "scans",
            sa.Column(
                "snapshot",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
        )
    if not index_exists(bind, "scans", "ix_scans_product_id"):
        op.create_index("ix_scans_product_id", "scans", ["product_id"], unique=False)
    if not index_exists(bind, "scans", "ix_scans_scan_status"):
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
