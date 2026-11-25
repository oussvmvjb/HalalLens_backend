import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Enum as SAEnum
from sqlalchemy import MetaData, inspect, pool, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import app.models  # noqa: F401 - ensure all ORM models are registered on Base.metadata
from app.core.config import get_settings
from app.db.base import Base

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI_PATH = BACKEND_DIR / "alembic.ini"
ALEMBIC_SCRIPT_LOCATION = BACKEND_DIR / "alembic"
MIGRATION_ADVISORY_LOCK_ID = 8429104721

_LOCAL_MIGRATION_LOCK = asyncio.Lock()

logger = logging.getLogger("halallens.database")
if not logger.handlers and not logging.getLogger().handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


class DatabaseInitializationError(RuntimeError):
    """Raised when database connectivity, migration, or schema verification fails."""


def normalize_database_url(database_url: str) -> str:
    url = database_url.strip()
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def redact_database_url(database_url: str) -> str:
    try:
        parsed = make_url(database_url)
        host = parsed.host or "local"
        port = f":{parsed.port}" if parsed.port else ""
        database = parsed.database or ""
        return f"{parsed.drivername}://{host}{port}/{database}"
    except Exception:
        return "<redacted>"


def _build_alembic_config(database_url: str) -> Config:
    if not ALEMBIC_INI_PATH.exists():
        raise DatabaseInitializationError(
            f"Alembic configuration file not found at {ALEMBIC_INI_PATH}"
        )
    cfg = Config(str(ALEMBIC_INI_PATH))
    cfg.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def _get_current_heads(sync_conn: Connection) -> tuple[str, ...]:
    migration_context = MigrationContext.configure(sync_conn)
    return migration_context.get_current_heads()


def _run_alembic_upgrade(sync_conn: Connection, alembic_cfg: Config) -> None:
    alembic_cfg.attributes["connection"] = sync_conn
    alembic_cfg.attributes["skip_file_config"] = True
    command.upgrade(alembic_cfg, "head")


def verify_schema_compatibility(sync_conn: Connection, metadata: MetaData) -> None:
    """Verify that all tables, columns, and PostgreSQL enum types exist."""
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}

    for table_name, table in metadata.tables.items():
        if table_name not in existing_tables:
            missing_tables.append(table_name)
            continue
        existing_col_names = {col["name"] for col in inspector.get_columns(table_name)}
        missing_cols = [col.name for col in table.columns if col.name not in existing_col_names]
        if missing_cols:
            missing_columns[table_name] = missing_cols

    missing_enums: list[str] = []
    missing_enum_values: dict[str, list[str]] = {}
    if sync_conn.dialect.name == "postgresql":
        existing_enums = {
            enum_info["name"]: set(enum_info.get("labels", ()))
            for enum_info in inspector.get_enums()
        }
        expected_enums: dict[str, tuple[str, ...]] = {}
        for table in metadata.tables.values():
            for col in table.columns:
                if isinstance(col.type, SAEnum) and col.type.name:
                    expected_enums[col.type.name] = tuple(col.type.enums)
        for enum_name, expected_labels in sorted(expected_enums.items()):
            if enum_name not in existing_enums:
                missing_enums.append(enum_name)
                continue
            absent_labels = [lbl for lbl in expected_labels if lbl not in existing_enums[enum_name]]
            if absent_labels:
                missing_enum_values[enum_name] = absent_labels

    diagnostics: list[str] = []
    if missing_tables:
        diagnostics.append(f"missing tables: {', '.join(sorted(missing_tables))}")
    if missing_columns:
        cols_detail = "; ".join(
            f"{tbl} ({', '.join(cols)})" for tbl, cols in sorted(missing_columns.items())
        )
        diagnostics.append(f"missing columns: {cols_detail}")
    if missing_enums:
        diagnostics.append(f"missing PostgreSQL enum types: {', '.join(missing_enums)}")
    if missing_enum_values:
        enum_detail = "; ".join(
            f"{name} ({', '.join(vals)})" for name, vals in sorted(missing_enum_values.items())
        )
        diagnostics.append(f"missing PostgreSQL enum values: {enum_detail}")

    if diagnostics:
        message = f"[DATABASE] Schema verification failed: {' | '.join(diagnostics)}"
        logger.error(message)
        raise DatabaseInitializationError(message)


async def _migrate_and_verify(init_engine: AsyncEngine, database_url: str) -> None:
    alembic_cfg = _build_alembic_config(database_url)
    script = ScriptDirectory.from_config(alembic_cfg)
    target_heads = tuple(script.get_heads())

    async with init_engine.connect() as conn:
        logger.info("[DATABASE] Checking Alembic migration version...")
        current_heads = await conn.run_sync(_get_current_heads)
        if conn.in_transaction():
            await conn.commit()

        if set(current_heads) != set(target_heads):
            if not current_heads:
                existing_app_tables = await conn.run_sync(
                    lambda sync_conn: set(inspect(sync_conn).get_table_names())
                    & set(Base.metadata.tables.keys())
                )
                if conn.in_transaction():
                    await conn.commit()
                if existing_app_tables:
                    logger.info(
                        "[DATABASE] Existing tables detected without Alembic version (%d tables); "
                        "reconciling schema safely...",
                        len(existing_app_tables),
                    )
            current_display = ", ".join(current_heads) if current_heads else "none"
            target_display = ", ".join(target_heads) if target_heads else "none"
            logger.info(
                "[DATABASE] Pending migrations detected (current=%s, target=%s).",
                current_display,
                target_display,
            )
            logger.info("[DATABASE] Applying pending migrations...")
            try:
                await conn.run_sync(_run_alembic_upgrade, alembic_cfg)
                if conn.in_transaction():
                    await conn.commit()
            except Exception as exc:
                if conn.in_transaction():
                    await conn.rollback()
                logger.error(
                    "[DATABASE] Migration failed while upgrading from %s to %s: %s",
                    current_display,
                    target_display,
                    exc,
                )
                raise DatabaseInitializationError(
                    f"Database migration failed (current={current_display}, target={target_display}): {exc}"
                ) from exc

            updated_heads = await conn.run_sync(_get_current_heads)
            if conn.in_transaction():
                await conn.commit()
            if set(updated_heads) != set(target_heads):
                raise DatabaseInitializationError(
                    f"Migration did not reach target head {target_heads}; current={updated_heads}"
                )
        else:
            logger.info(
                "[DATABASE] Alembic migration version is up to date (%s).",
                ", ".join(current_heads) if current_heads else "head",
            )

        await conn.run_sync(verify_schema_compatibility, Base.metadata)
        if conn.in_transaction():
            await conn.commit()


async def initialize_database(database_url: str | None = None) -> None:
    """Connect to the database, apply pending Alembic migrations, and verify schema."""
    raw_url = database_url if database_url is not None else get_settings().database_url
    normalized_url = normalize_database_url(raw_url)
    safe_target = redact_database_url(normalized_url)
    is_postgres = normalized_url.startswith("postgresql")

    init_engine = create_async_engine(
        normalized_url,
        poolclass=pool.NullPool,
        pool_pre_ping=True,
    )

    try:
        if is_postgres:
            logger.info("[DATABASE] Connecting to Supabase PostgreSQL (%s)...", safe_target)
        else:
            logger.info("[DATABASE] Connecting to database (%s)...", safe_target)

        try:
            async with init_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                if conn.in_transaction():
                    await conn.commit()
        except Exception as exc:
            logger.error("[DATABASE] Connection failed (%s): %s", safe_target, exc)
            raise DatabaseInitializationError(
                f"Failed to connect to database ({safe_target}): {exc}"
            ) from exc

        logger.info("[DATABASE] Connection successful.")

        if is_postgres:
            async with init_engine.connect() as lock_conn:
                await lock_conn.execute(
                    text("SELECT pg_advisory_lock(:lock_id)"),
                    {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
                )
                if lock_conn.in_transaction():
                    await lock_conn.commit()
                try:
                    await _migrate_and_verify(init_engine, normalized_url)
                finally:
                    await lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
                    )
                    if lock_conn.in_transaction():
                        await lock_conn.commit()
        else:
            async with _LOCAL_MIGRATION_LOCK:
                await _migrate_and_verify(init_engine, normalized_url)

        logger.info("[DATABASE] Database schema is synchronized.")
    finally:
        await init_engine.dispose()
