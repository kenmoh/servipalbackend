from app.auth.auth import create_access_token
from app.models.models import User
from typing import Dict, List
from typing import AsyncGenerator, Generator
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
import pytest
from httpx import AsyncClient
from app.main import app
from app.database.database import Base, TestingSessionLocal, get_db, test_engine
from app.config.config import settings


@pytest.fixture(autouse=True)
async def setup_db():
    """Automatically setup and cleanup database for every test"""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session() -> AsyncGenerator:
    """Create a fresh database session for each test"""
    async with TestingSessionLocal() as session:
        try:
            yield session
            await session.rollback()  # Rollback any changes made during the test
        finally:
            await session.close()


@pytest.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator:
    """Async client for testing async endpoints"""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    base_url = "http://localhost:8000"  # or settings.API_URL if configured

    async with AsyncClient(base_url=base_url) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def test_client(db_session: AsyncSession) -> Generator:
    """Sync client for testing sync endpoints"""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def test_users() -> List[Dict]:
    """Different user types for testing"""
    return [
        {
            "email": "customer@gmail.com",
            "password": "@Ttring12",
            "user_type": "customer"
        },
        {
            "email": "customer@example.com",
            "password": "@Testpass123",
            "user_type": "customer",

        },
        {
            "email": "dispatch@example.com",
            "password": "@Testpass123",
            "user_type": "dispatch",

        }
    ]


@pytest.fixture
def vendor_token(test_users: List[Dict]) -> str:
    return create_access_token({"sub": test_users[0]["email"]})


@pytest.fixture
def customer_token(test_users: List[Dict]) -> str:
    return create_access_token({"sub": test_users[1]["email"]})


@pytest.fixture
def admin_token(test_users: List[Dict]) -> str:
    return create_access_token({"sub": test_users[2]["email"]})


@pytest.fixture
async def authorized_vendor_client(async_client: AsyncClient, vendor_token: str):
    async_client.headers = {
        **async_client.headers,
        "Authorization": f"Bearer {vendor_token}"
    }
    return async_client


@pytest.fixture
def authorized_customer_client(test_client, customer_token: str):
    test_client.headers = {
        **test_client.headers,
        "Authorization": f"Bearer {customer_token}"
    }
    return test_client


@pytest.fixture
def authorized_admin_client(test_client, admin_token: str):
    test_client.headers = {
        **test_client.headers,
        "Authorization": f"Bearer {admin_token}"
    }
    return test_client


@pytest.fixture
def unauthorized_client(test_client):
    """Client without authorization token"""
    return test_client
