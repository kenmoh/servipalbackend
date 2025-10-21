
import asyncio
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import text

from app.main import app
from app.models.models import Base
from app.database.database import get_db, create_test_session, create_test_engine
from app.config.config import settings
from app.schemas.status_schema import UserType


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create test engine once per session with proper connection settings."""
    engine = create_async_engine(
        settings.TEST_DATABASE_URL,
        poolclass=NullPool,
        echo=False,
        # Critical: Disable statement caching and use fresh connections
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "command_timeout": 60,
        }
    )
    
    # Clean database before creating schema
    async with engine.begin() as conn:
        # Drop all connections first
        await conn.execute(text("""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = current_database()
            AND pid <> pg_backend_pid()
        """))
        
        # Drop all objects
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Cleanup
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_db(test_engine):
    """Create fresh database session for each test with transaction rollback."""
    # Create a new connection for each test to avoid type cache issues
    connection = await test_engine.connect()
    
    # Begin a transaction
    transaction = await connection.begin()
    
    # Create session bound to this connection
    async_session = async_sessionmaker(
        connection,
        expire_on_commit=False,
        class_=AsyncSession
    )
    
    session = async_session()
    
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def async_client(async_db):
    """Provides an async HTTP client that uses the test database session."""
    from app.database import database

    # Create a fresh test engine (same as in async_db setup)
    test_engine = create_test_engine()

    # Patch the global engine used by the app
    original_engine = getattr(database, 'engine', None)
    database.engine = test_engine

    # Also ensure the sessionmaker uses this engine
    test_session_maker = create_test_session(test_engine)
    original_session_maker = getattr(database, 'async_session_maker', None)
    database.async_session_maker = test_session_maker

    async def _get_test_db():
        yield async_db

    app.dependency_overrides[get_db] = _get_test_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client

    # Cleanup: restore original engine if needed (optional in tests)
    app.dependency_overrides.clear()
    if original_engine:
        await original_engine.dispose()
    if hasattr(database, 'engine') and database.engine:
        await database.engine.dispose()
    database.engine = original_engine
    database.async_session_maker = original_session_maker



import uuid
from app.schemas.status_schema import UserType


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def authenticated_user(async_client: AsyncClient):
    """
    Fixture to create a user, log them in, and return authentication details.
    """
    unique_id = str(uuid.uuid4())[:8]
    user_payload = {
        "email": f"testuser_{unique_id}@example.com",
        "password": "Password123!",
        "user_type": UserType.CUSTOMER.value,
        "phone_number": f"+12345{unique_id[:5]}",
    }

    # Create user
    register_response = await async_client.post("/api/auth/register", json=user_payload)
    assert register_response.status_code == 201
    user_data = register_response.json()

    # Log in user
    login_data = {"username": user_payload["email"], "password": user_payload["password"]}
    login_response = await async_client.post("/api/auth/login", data=login_data)
    assert login_response.status_code == 200
    auth_data = login_response.json()

    return {
        "user": user_data,
        "access_token": auth_data["access_token"],
        "refresh_token": auth_data["refresh_token"],
        "headers": {"Authorization": f"Bearer {auth_data['access_token']}"},
    }



