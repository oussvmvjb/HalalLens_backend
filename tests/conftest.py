from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
import app.models  # noqa: F401
from app.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    test_sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with test_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()
