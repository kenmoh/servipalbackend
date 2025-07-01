from app.auth.auth import create_access_token
from app.models.models import User, Wallet
from typing import Dict, Tuple, Any
from typing import AsyncGenerator, Any
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select  # Removed List as it's not used directly here
import pytest
from httpx import AsyncClient, ASGITransport
from alembic.config import Config
from alembic import command

from app.main import app
from app.database.database import Base, TestingSessionLocal, get_db, test_engine
from app.config.config import settings
from app.services.auth_service import hash_password
from app.schemas.user_schemas import UserType
from app.schemas.status_schema import AccountStatus
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from uuid import uuid4

# Ensure all your models are imported so Base.metadata is populated.
# This might be done in app.main, or you might need to explicitly
# import your models module here or in app.models.__init__.py

# @pytest.fixture(autouse=True)
# async def setup_db():
#     """Automatically setup and cleanup database for every test"""
#     async with test_engine.begin() as conn:
#         await conn.run_sync(Base.metadata.create_all)
#     yield
#     async with test_engine.begin() as conn:
#         await conn.run_sync(Base.metadata.drop_all)


# If you are using pytest-asyncio, it's generally recommended to let it manage
# the event loop. Consider removing this custom event_loop fixture if pytest-asyncio
# is installed and you haven't configured a specific asyncio_mode that requires it.
# @pytest.fixture(scope="session")
# def event_loop():
#     """Create an instance of the default event loop for each test case."""
#     try:
#         loop = asyncio.get_event_loop()
#     except RuntimeError:
#         loop = asyncio.new_event_loop()
#     asyncio.set_event_loop(loop)
#     yield loop
#     loop.close()
print(f"Available tables in metadata: {Base.metadata.tables.keys()}")


@pytest.fixture
async def setup_db():
    """Setup and teardown the database"""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session(setup_db) -> AsyncGenerator[AsyncSession, None]:
    """Create a function-scoped database session"""
    async with TestingSessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create a function-scoped async client"""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:8000",
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
# Changed str to Any for user_type enum
def test_user_data() -> Dict[str, Dict[str, Any]]:
    """Provides data for different user types for testing."""
    common_password = "@TestPassword123"
    return {
        "customer": {
            "email": "customer@example.com",
            "password": common_password,
            "user_type": UserType.CUSTOMER,
        },
        "vendor": {
            "email": "vendor@example.com",
            "password": common_password,
            "user_type": UserType.VENDOR,
        },
        "dispatch": {
            "email": "dispatch@example.com",
            "password": common_password,
            "user_type": UserType.DISPATCH,
        },
        "admin": {
            "email": "admin@example.com",
            "password": common_password,
            "user_type": UserType.ADMIN,
        },
    }


async def _create_user_in_db(db: AsyncSession, user_details: Dict[str, Any]) -> User:
    """Helper function to create a user in the database for tests."""
    # Instantiate User and set attributes separately to avoid __init__ issues
    # revealed by TypeError: 'is_active' is an invalid keyword argument for User()
    user = User()
    user.email = user_details["email"]
    user.password = hash_password(user_details["password"])
    # Directly assign the enum member
    user.user_type = user_details["user_type"]
    user.is_verified = True
    user.account_status = AccountStatus.CONFIRMED

    db.add(user)
    await db.flush()  # To get user.id for wallet or other relations

    if user.user_type != UserType.RIDER:
        wallet_exists = await db.execute(select(Wallet).where(Wallet.id == user.id))
        if not wallet_exists.scalar_one_or_none():
            wallet = Wallet(id=user.id, balance=0, escrow_balance=0)
            db.add(wallet)

    await db.commit()
    await db.refresh(user)
    return user


@pytest.fixture
async def customer_user_and_token(
    db_session: AsyncSession, test_user_data: Dict[str, Dict[str, Any]]
) -> Tuple[User, str]:
    user_details = test_user_data["customer"]
    user = await _create_user_in_db(db_session, user_details)
    token = create_access_token({"sub": user.email})
    return user, token


@pytest.fixture
async def vendor_user_and_token(
    db_session: AsyncSession, test_user_data: Dict[str, Dict[str, Any]]
) -> Tuple[User, str]:
    user_details = test_user_data["vendor"]
    user = await _create_user_in_db(db_session, user_details)
    token = create_access_token({"sub": user.email})
    return user, token


@pytest.fixture
async def dispatch_user_and_token(
    db_session: AsyncSession, test_user_data: Dict[str, Dict[str, Any]]
) -> Tuple[User, str]:
    user_details = test_user_data["dispatch"]
    user = await _create_user_in_db(db_session, user_details)
    token = create_access_token({"sub": user.email})
    return user, token


@pytest.fixture
async def admin_user_and_token(
    db_session: AsyncSession, test_user_data: Dict[str, Dict[str, Any]]
) -> Tuple[User, str]:
    user_details = test_user_data["admin"]
    user = await _create_user_in_db(db_session, user_details)
    token = create_access_token({"sub": user.email})
    return user, token


@pytest.fixture
async def authorized_customer_client(
    async_client: AsyncClient, customer_user_and_token: Tuple[User, str]
):
    _, token = customer_user_and_token
    async_client.headers = {
        **async_client.headers,
        "Authorization": f"Bearer {token}",
    }
    return async_client


@pytest.fixture
async def authorized_vendor_client(
    async_client: AsyncClient, vendor_user_and_token: Tuple[User, str]
):
    _, token = vendor_user_and_token
    async_client.headers = {
        **async_client.headers,
        "Authorization": f"Bearer {token}",
    }
    return async_client


@pytest.fixture
async def authorized_dispatch_client(
    async_client: AsyncClient, dispatch_user_and_token: Tuple[User, str]
):
    _, token = dispatch_user_and_token
    async_client.headers = {
        **async_client.headers,
        "Authorization": f"Bearer {token}",
    }
    return async_client


@pytest.fixture
async def authorized_admin_client(
    async_client: AsyncClient, admin_user_and_token: Tuple[User, str]
):
    _, token = admin_user_and_token
    async_client.headers = {
        **async_client.headers,
        "Authorization": f"Bearer {token}",
    }
    return async_client


# The `async_client` fixture can be used directly for unauthorized async requests.
# The `sync_test_client` fixture can be used directly for unauthorized sync requests.


@pytest.fixture
def unauthorized_sync_client(sync_test_client: TestClient) -> TestClient:
    """Explicitly named sync client without authorization token for clarity if preferred."""
    return sync_test_client


# Use the test database URL
TEST_DATABASE_URL = settings.TEST_DATABASE_URL

engine_test = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool, future=True)
TestingSessionLocal = async_sessionmaker(engine_test, expire_on_commit=False, class_=AsyncSession)

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session", autouse=True)
async def setup_test_db():
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture(scope="function")
async def db_session():
    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()

@pytest.fixture(scope="function")
async def async_client(db_session):
    async def override_get_db():
        yield db_session
    app.dependency_overrides = {}
    app.dependency_overrides["get_db"] = override_get_db
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

@pytest.fixture
def test_user():
    return {
        "email": f"user_{uuid4()}@test.com",
        "password": "testpassword",
        "user_type": UserType.CUSTOMER,
    }

@pytest.fixture
def test_vendor():
    return {
        "email": f"vendor_{uuid4()}@test.com",
        "password": "testpassword",
        "user_type": UserType.RESTAURANT_VENDOR,
    }

@pytest.fixture
def test_wallet():
    return Wallet(balance=10000, escrow_balance=0)
