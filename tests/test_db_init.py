import logging
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.db.init_db import (
    DatabaseInitializationError,
    _build_alembic_config,
    initialize_database,
    normalize_database_url,
    redact_database_url,
    verify_schema_compatibility,
)
from app.db.migration_helpers import ensure_postgres_enum


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


def test_normalize_and_redact_database_url() -> None:
    raw = "postgres://postgres.proj:super-secret-password@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
    normalized = normalize_database_url(raw)
    assert normalized.startswith("postgresql+asyncpg://")

    redacted = redact_database_url(normalized)
    assert "super-secret-password" not in redacted
    assert "postgres.proj" not in redacted
    assert "aws-0-eu-west-1.pooler.supabase.com:5432/postgres" in redacted


@pytest.mark.asyncio
async def test_initialize_database_applies_pending_migrations_and_preserves_data(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = tmp_path / "pending_migrations.db"
    db_url = _sqlite_url(db_path)

    # Simulate a database stuck at an older migration (0002) with existing user/product/scan rows.
    cfg = _build_alembic_config(db_url)
    engine = create_async_engine(db_url)
    user_id = str(uuid4()).replace("-", "")
    product_id = str(uuid4()).replace("-", "")
    scan_id = str(uuid4()).replace("-", "")
    try:
        async with engine.begin() as conn:
            def _upgrade_to_0002(sync_conn) -> None:
                cfg.attributes["connection"] = sync_conn
                cfg.attributes["skip_file_config"] = True
                command.upgrade(cfg, "0002_barcode_product_data")

            await conn.run_sync(_upgrade_to_0002)
            await conn.execute(
                text(
                    "INSERT INTO users (id, full_name, email, hashed_password, preferred_language, is_active) "
                    "VALUES (:id, 'Test User', 'test@example.com', 'hash', 'en', 1)"
                ),
                {"id": user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO products (id, barcode, name, external_data) "
                    "VALUES (:id, '1234567890123', 'Sample Product', '{}')"
                ),
                {"id": product_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO scans (id, user_id, product_id, barcode, scan_status) "
                    "VALUES (:id, :user_id, :product_id, '1234567890123', 'COMPLETED')"
                ),
                {"id": scan_id, "user_id": user_id, "product_id": product_id},
            )
    finally:
        await engine.dispose()

    with caplog.at_level(logging.INFO, logger="halallens.database"):
        await initialize_database(db_url)

    assert "[DATABASE] Connection successful." in caplog.text
    assert "[DATABASE] Checking Alembic migration version..." in caplog.text
    assert "[DATABASE] Applying pending migrations..." in caplog.text
    assert "[DATABASE] Database schema is synchronized." in caplog.text

    # Verify existing data is intact and new columns from 0004 exist with expected defaults.
    verify_engine = create_async_engine(db_url)
    try:
        async with verify_engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT barcode, scan_type, scan_status, snapshot FROM scans WHERE id = :id"),
                    {"id": scan_id},
                )
            ).one()
            assert row.barcode == "1234567890123"
            assert row.scan_type == "BARCODE"
            assert row.scan_status == "COMPLETED"
            assert row.snapshot == "{}"
    finally:
        await verify_engine.dispose()


@pytest.mark.asyncio
async def test_initialize_database_idempotent_when_synchronized(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_url = _sqlite_url(tmp_path / "synced.db")

    await initialize_database(db_url)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="halallens.database"):
        await initialize_database(db_url)

    assert "Alembic migration version is up to date (0004_scan_history)." in caplog.text
    assert "[DATABASE] Applying pending migrations..." not in caplog.text
    assert "[DATABASE] Database schema is synchronized." in caplog.text


@pytest.mark.asyncio
async def test_initialize_database_connection_failure_hides_credentials(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad_dir = tmp_path / "nonexistent_dir" / "missing.db"
    bad_url = f"sqlite+aiosqlite:///{bad_dir.as_posix()}"

    with caplog.at_level(logging.INFO, logger="halallens.database"):
        with pytest.raises(DatabaseInitializationError, match="Failed to connect to database"):
            await initialize_database(bad_url)

    assert "[DATABASE] Connection failed" in caplog.text


@pytest.mark.asyncio
async def test_verify_schema_compatibility_detects_missing_column(tmp_path: Path) -> None:
    db_url = _sqlite_url(tmp_path / "missing_col.db")
    cfg = _build_alembic_config(db_url)
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            def _upgrade_to_0001(sync_conn) -> None:
                cfg.attributes["connection"] = sync_conn
                cfg.attributes["skip_file_config"] = True
                command.upgrade(cfg, "0001_initial")

            await conn.run_sync(_upgrade_to_0001)
            with pytest.raises(DatabaseInitializationError, match="scan_type"):
                await conn.run_sync(verify_schema_compatibility, Base.metadata)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recover_missing_alembic_version_and_missing_scans_table(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_url = _sqlite_url(tmp_path / "partial_no_scans.db")
    cfg = _build_alembic_config(db_url)
    engine = create_async_engine(db_url)
    user_id = str(uuid4()).replace("-", "")
    product_id = str(uuid4()).replace("-", "")
    try:
        async with engine.begin() as conn:
            def _upgrade_to_0002(sync_conn) -> None:
                cfg.attributes["connection"] = sync_conn
                cfg.attributes["skip_file_config"] = True
                command.upgrade(cfg, "0002_barcode_product_data")

            await conn.run_sync(_upgrade_to_0002)
            await conn.execute(
                text(
                    "INSERT INTO users (id, full_name, email, hashed_password, preferred_language, is_active) "
                    "VALUES (:id, 'Existing User', 'existing@example.com', 'hash', 'en', 1)"
                ),
                {"id": user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO products (id, barcode, name, external_data) "
                    "VALUES (:id, '9998887776665', 'Existing Product', '{}')"
                ),
                {"id": product_id},
            )
            # Simulate missing scan tables and missing alembic_version table
            await conn.execute(text("DROP TABLE scan_evidence"))
            await conn.execute(text("DROP TABLE user_reports"))
            await conn.execute(text("DROP TABLE scan_results"))
            await conn.execute(text("DROP TABLE scans"))
            await conn.execute(text("DROP TABLE alembic_version"))
    finally:
        await engine.dispose()

    with caplog.at_level(logging.INFO, logger="halallens.database"):
        await initialize_database(db_url)

    assert "Existing tables detected without Alembic version" in caplog.text
    assert "[DATABASE] Database schema is synchronized." in caplog.text

    verify_engine = create_async_engine(db_url)
    try:
        async with verify_engine.connect() as conn:
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            assert version == "0004_scan_history"
            user_email = (
                await conn.execute(text("SELECT email FROM users WHERE id = :id"), {"id": user_id})
            ).scalar_one()
            assert user_email == "existing@example.com"
            scan_count = (await conn.execute(text("SELECT COUNT(*) FROM scans"))).scalar_one()
            assert scan_count == 0
    finally:
        await verify_engine.dispose()


@pytest.mark.asyncio
async def test_recover_missing_alembic_version_and_missing_scan_columns(
    tmp_path: Path,
) -> None:
    db_url = _sqlite_url(tmp_path / "partial_old_scans.db")
    cfg = _build_alembic_config(db_url)
    engine = create_async_engine(db_url)
    scan_id = str(uuid4()).replace("-", "")
    try:
        async with engine.begin() as conn:
            def _upgrade_to_0001(sync_conn) -> None:
                cfg.attributes["connection"] = sync_conn
                cfg.attributes["skip_file_config"] = True
                command.upgrade(cfg, "0001_initial")

            await conn.run_sync(_upgrade_to_0001)
            await conn.execute(
                text(
                    "INSERT INTO scans (id, barcode, scan_status) "
                    "VALUES (:id, '5554443332221', 'COMPLETED')"
                ),
                {"id": scan_id},
            )
            await conn.execute(text("DROP TABLE alembic_version"))
    finally:
        await engine.dispose()

    await initialize_database(db_url)

    verify_engine = create_async_engine(db_url)
    try:
        async with verify_engine.connect() as conn:
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            assert version == "0004_scan_history"
            row = (
                await conn.execute(
                    text("SELECT barcode, scan_type, snapshot FROM scans WHERE id = :id"),
                    {"id": scan_id},
                )
            ).one()
            assert row.barcode == "5554443332221"
            assert row.scan_type == "BARCODE"
            assert row.snapshot == "{}"
    finally:
        await verify_engine.dispose()


def test_ensure_postgres_enum_skips_existing_and_adds_missing_values() -> None:
    executed_sql: list[str] = []

    class _FakeScalarResult:
        def __init__(self, value):
            self._value = value

        def scalar_one(self):
            return self._value

        def scalars(self):
            return self

        def all(self):
            return self._value

    class _FakeConnection:
        dialect = postgresql.dialect()

        def execute(self, stmt, params=None):
            sql_text = str(stmt)
            executed_sql.append(sql_text)
            if "SELECT EXISTS" in sql_text:
                return _FakeScalarResult(True)
            if "SELECT e.enumlabel" in sql_text:
                return _FakeScalarResult(["RECOGNIZED", "UNRECOGNIZED", "PENDING"])
            return _FakeScalarResult(None)

    ensure_postgres_enum(
        _FakeConnection(),  # type: ignore[arg-type]
        "authority_status",
        ("RECOGNIZED", "UNRECOGNIZED", "PENDING", "REVOKED"),
    )

    assert not any("CREATE TYPE" in sql for sql in executed_sql)
    assert any("ALTER TYPE authority_status ADD VALUE IF NOT EXISTS 'REVOKED'" in sql for sql in executed_sql)

