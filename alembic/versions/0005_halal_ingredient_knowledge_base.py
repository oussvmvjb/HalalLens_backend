"""Add halal ingredient knowledge base tables and fields.

Revision ID: 0005_halal_knowledge_base
Revises: 0004_scan_history
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.migration_helpers import (
    column_exists,
    ensure_postgres_enum,
    index_exists,
    table_exists,
)

revision = "0005_halal_knowledge_base"
down_revision = "0004_scan_history"
branch_labels = None
depends_on = None

_INGREDIENT_HALAL_STATUS = ("halal", "haram", "doubtful", "conditional", "unknown")
_EVIDENCE_REVIEW_STATUS = ("verified", "pending", "disputed", "rejected")
_EVIDENCE_TYPE = ("INGREDIENT", "CERTIFICATION", "SOURCE", "USER_REPORT", "OTHER")


def upgrade() -> None:
    bind = op.get_bind()
    ensure_postgres_enum(bind, "ingredient_halal_status", _INGREDIENT_HALAL_STATUS)
    ensure_postgres_enum(bind, "evidence_review_status", _EVIDENCE_REVIEW_STATUS)
    ensure_postgres_enum(bind, "evidence_type", _EVIDENCE_TYPE)

    ingredient_halal_status = postgresql.ENUM(
        *_INGREDIENT_HALAL_STATUS, name="ingredient_halal_status", create_type=False
    )
    evidence_review_status = postgresql.ENUM(
        *_EVIDENCE_REVIEW_STATUS, name="evidence_review_status", create_type=False
    )
    evidence_type = postgresql.ENUM(
        *_EVIDENCE_TYPE, name="evidence_type", create_type=False
    )
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    if not column_exists(bind, "ingredients", "ingredient_type"):
        op.add_column("ingredients", sa.Column("ingredient_type", sa.String(length=64), nullable=True))
    if not column_exists(bind, "ingredients", "e_number"):
        op.add_column("ingredients", sa.Column("e_number", sa.String(length=32), nullable=True))
    if not column_exists(bind, "ingredients", "ins_number"):
        op.add_column("ingredients", sa.Column("ins_number", sa.String(length=32), nullable=True))
    if not column_exists(bind, "ingredients", "aliases"):
        op.add_column(
            "ingredients",
            sa.Column("aliases", json_type, server_default=sa.text("'[]'"), nullable=False),
        )
    if not column_exists(bind, "ingredients", "halal_status"):
        op.add_column(
            "ingredients",
            sa.Column(
                "halal_status",
                ingredient_halal_status,
                server_default=sa.text("'unknown'"),
                nullable=False,
            ),
        )
    if not column_exists(bind, "ingredients", "source_requirements"):
        op.add_column("ingredients", sa.Column("source_requirements", sa.Text(), nullable=True))
    if not column_exists(bind, "ingredients", "conditions"):
        op.add_column(
            "ingredients",
            sa.Column("conditions", json_type, server_default=sa.text("'[]'"), nullable=False),
        )
    if not column_exists(bind, "ingredients", "confidence_level"):
        op.add_column(
            "ingredients",
            sa.Column("confidence_level", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        )

    if not index_exists(bind, "ingredients", op.f("ix_ingredients_e_number")):
        op.create_index(op.f("ix_ingredients_e_number"), "ingredients", ["e_number"], unique=False)
    if not index_exists(bind, "ingredients", op.f("ix_ingredients_ins_number")):
        op.create_index(op.f("ix_ingredients_ins_number"), "ingredients", ["ins_number"], unique=False)

    if not table_exists(bind, "ingredient_sources"):
        op.create_table(
            "ingredient_sources",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("ingredient_id", sa.Uuid(), nullable=False),
            sa.Column("source_name", sa.String(length=255), nullable=False),
            sa.Column("source_url", sa.String(length=2048), nullable=True),
            sa.Column("evidence_summary", sa.Text(), nullable=False),
            sa.Column("evidence_type", evidence_type, nullable=False),
            sa.Column("reviewer_or_organization", sa.String(length=255), nullable=True),
            sa.Column("verification_date", sa.Date(), nullable=True),
            sa.Column(
                "review_status",
                evidence_review_status,
                server_default=sa.text("'pending'"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["ingredient_id"],
                ["ingredients.id"],
                name=op.f("fk_ingredient_sources_ingredient_id_ingredients"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_ingredient_sources")),
        )
    if not index_exists(bind, "ingredient_sources", "ix_ingredient_sources_ingredient_id"):
        op.create_index("ix_ingredient_sources_ingredient_id", "ingredient_sources", ["ingredient_id"])
    if not index_exists(bind, "ingredient_sources", "ix_ingredient_sources_review_status"):
        op.create_index("ix_ingredient_sources_review_status", "ingredient_sources", ["review_status"])

    if not table_exists(bind, "halal_rules"):
        op.create_table(
            "halal_rules",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("rule_name", sa.String(length=120), nullable=False),
            sa.Column("rule_description", sa.Text(), nullable=False),
            sa.Column("rule_priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
            sa.Column(
                "applicable_conditions",
                json_type,
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
            sa.Column(
                "rule_version",
                sa.String(length=32),
                server_default=sa.text("'1.0.0'"),
                nullable=False,
            ),
            sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            sa.PrimaryKeyConstraint("id", name=op.f("pk_halal_rules")),
            sa.UniqueConstraint("rule_name", name=op.f("uq_halal_rules_rule_name")),
        )
    if not index_exists(bind, "halal_rules", "ix_halal_rules_active_priority"):
        op.create_index("ix_halal_rules_active_priority", "halal_rules", ["active", "rule_priority"])


def downgrade() -> None:
    op.drop_index("ix_halal_rules_active_priority", table_name="halal_rules")
    op.drop_table("halal_rules")
    op.drop_index("ix_ingredient_sources_review_status", table_name="ingredient_sources")
    op.drop_index("ix_ingredient_sources_ingredient_id", table_name="ingredient_sources")
    op.drop_table("ingredient_sources")
    op.drop_index(op.f("ix_ingredients_ins_number"), table_name="ingredients")
    op.drop_index(op.f("ix_ingredients_e_number"), table_name="ingredients")
    op.drop_column("ingredients", "confidence_level")
    op.drop_column("ingredients", "conditions")
    op.drop_column("ingredients", "source_requirements")
    op.drop_column("ingredients", "halal_status")
    op.drop_column("ingredients", "aliases")
    op.drop_column("ingredients", "ins_number")
    op.drop_column("ingredients", "e_number")
    op.drop_column("ingredients", "ingredient_type")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(name="evidence_review_status").drop(bind, checkfirst=True)
        postgresql.ENUM(name="ingredient_halal_status").drop(bind, checkfirst=True)
