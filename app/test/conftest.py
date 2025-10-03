import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import AsyncMock, MagicMock

from app.main import app
from app.database.database import get_db, Base
from app.config.config import settings
from app.models.models import User
from app.schemas.status_schema import UserType, AccountStatus
from app.auth.auth import create_access_token, get_password_hash


# Use the existing test database from settings
TEST_DATABASE_URL = settings.TEST_DATABASE_URL

# Create test engine
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    pool_pre_ping=True
)

TestingSessionLocal = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture(scope="session")
async def setup_test_db():
    """Create test database tables once per session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(setup_test_db):
    """Create a test database session with cleanup."""
    async with TestingSessionLocal() as session:
        yield session
        # Clean up after each test
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session):
    """Create test client with database override."""
    def override_get_db():
        return db_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session):
    """Create a test user."""
    user = User(
        email="test@example.com",
        password=get_password_hash("@Testpassword123"),
        user_type=UserType.CUSTOMER,
        is_verified=True,
        is_email_verified=True,
        account_status=AccountStatus.CONFIRMED
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_vendor(db_session):
    """Create a test vendor user."""
    vendor = User(
        email="vendor@example.com",
        password=get_password_hash("@Vendorpassword123"),
        user_type=UserType.RESTAURANT_VENDOR,
        is_verified=True,
        is_email_verified=True,
        account_status=AccountStatus.CONFIRMED
    )
    db_session.add(vendor)
    await db_session.commit()
    await db_session.refresh(vendor)
    return vendor


@pytest_asyncio.fixture
async def test_rider(db_session):
    """Create a test rider user."""
    rider = User(
        email="rider@example.com",
        password=get_password_hash("@Riderpassword123"),
        user_type=UserType.RIDER,
        is_verified=True,
        is_email_verified=True,
        account_status=AccountStatus.CONFIRMED
    )
    db_session.add(rider)
    await db_session.commit()
    await db_session.refresh(rider)
    return rider


@pytest_asyncio.fixture
async def auth_headers(test_user):
    """Create authorization headers for test user."""
    token = create_access_token(data={"sub": test_user.email})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def vendor_auth_headers(test_vendor):
    """Create authorization headers for test vendor."""
    token = create_access_token(data={"sub": test_vendor.email})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def rider_auth_headers(test_rider):
    """Create authorization headers for test rider."""
    token = create_access_token(data={"sub": test_rider.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    return AsyncMock()


@pytest.fixture
def mock_s3():
    """Mock S3 service."""
    return MagicMock()


@pytest.fixture
def mock_email_service():
    """Mock email service."""
    return AsyncMock()


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
