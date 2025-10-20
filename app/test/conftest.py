import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.pool import NullPool

from app.auth.auth import get_current_user
from app.database.database import get_db
from app.main import app
from app.models.models import Base, User
from app.config.config import settings
from app.schemas.user_schemas import UserCreate
from app.schemas.status_schema import UserType, AccountStatus
from app.services.auth_service import create_user


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the entire test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create a fresh database engine for the test session."""

    test_engine = create_async_engine(
        settings.TEST_DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield test_engine

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Create an isolated database session for each test."""
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_maker() as session:
        # Start a nested transaction
        async with session.begin():
            # Use nested transaction for isolation
            nested = await session.begin_nested()
            yield session
            # Rollback the nested transaction after the test
            await nested.rollback()
        # Ensure outer transaction is also rolled back
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    Create a test client that uses the test database session.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def test_user(session: AsyncSession) -> User:
    """Create a test user for testing."""
    user_data = UserCreate(
        email="testuser@example.com",
        password="Password123!",
        user_type=UserType.CUSTOMER,
        phone_number="+1234567890",
        full_name="Test User"  # Added full_name as it's required
    )
    user = await create_user(db=session, user_data=user_data)
    await session.refresh(user)
    return user


@pytest_asyncio.fixture(scope="function")
async def another_user(session: AsyncSession) -> User:
    """
    Create another test user for authorization tests.
    """
    user_data = UserCreate(
        email="anotheruser@example.com",
        password="Password123!",
        user_type=UserType.RIDER,
        phone_number="+0987654321",
    )
    user = await create_user(db=session, user_data=user_data)
    return user


@pytest_asyncio.fixture(scope="function")
async def authenticated_client(
    client: AsyncClient, test_user: User
) -> AsyncClient:
    """
    Create an authenticated test client.
    """

    async def override_get_current_user() -> User:
        return test_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    return client
