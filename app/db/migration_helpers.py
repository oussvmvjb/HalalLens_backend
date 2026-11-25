from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection


def table_exists(bind: Connection, table_name: str) -> bool:
    """Return True if the given table already exists in the current schema."""
    return inspect(bind).has_table(table_name)


def column_exists(bind: Connection, table_name: str, column_name: str) -> bool:
    """Return True if the given column exists on the table."""
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def index_exists(bind: Connection, table_name: str, index_name: str) -> bool:
    """Return True if the given index exists on the table."""
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def ensure_postgres_enum(bind: Connection, name: str, values: tuple[str, ...]) -> None:
    """Create a PostgreSQL enum type if missing, or add any missing values safely.

    Never drops or recreates an existing enum type so existing rows are preserved.
    """
    if bind.dialect.name != "postgresql":
        return

    exists = bind.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_type t "
            "  WHERE t.typname = :name AND pg_type_is_visible(t.oid)"
            ")"
        ),
        {"name": name},
    ).scalar_one()

    if not exists:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)
        return

    existing_labels = set(
        bind.execute(
            text(
                "SELECT e.enumlabel "
                "FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = :name AND pg_type_is_visible(t.oid)"
            ),
            {"name": name},
        ).scalars().all()
    )

    quoted_name = bind.dialect.identifier_preparer.quote(name)
    for value in values:
        if value not in existing_labels:
            escaped_value = value.replace("'", "''")
            bind.execute(text(f"ALTER TYPE {quoted_name} ADD VALUE IF NOT EXISTS '{escaped_value}'"))
