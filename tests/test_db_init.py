import logging
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
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
