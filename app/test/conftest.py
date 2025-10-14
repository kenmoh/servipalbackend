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
    """
    Create a fresh database engine for the entire test session.
    The schema is created once and dropped after all tests are done.
    """
    test_engine = create_async_engine(
        url=settings.TEST_DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield test_engine

    await test_engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """
    Yields a transaction-isolated session for each test function.
    """
    async_session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session_factory() as test_session:
        await test_session.begin_nested()
        yield test_session
        await test_session.rollback()


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
    """
    Create a test user and save it to the database.
    """
    user_data = UserCreate(
        email="testuser@example.com",
        password="Password123!",
        user_type=UserType.CUSTOMER,
        phone_number="+1234567890",
    )
    user = await create_user(db=session, user_data=user_data)
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
