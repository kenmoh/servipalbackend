import pytest
from fastapi.testclient import TestClient
from app.services.auth_service import hash_password, verify_password
from httpx import AsyncClient
from fastapi import status

# Remove warnings filter as we'll fix the root cause
BASE_URL = "/api"


@pytest.fixture(scope="module", autouse=True)
async def setup_test():
    """Setup any test requirements"""
    yield


@pytest.mark.asyncio
async def test_user_registration(async_client: AsyncClient, test_users):
    """Test user registration"""
    response = await async_client.post(
        f"{BASE_URL}/auth/register",
        json=test_users[0]
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["email"] == test_users[0]["email"]
    assert response.json()["user_type"] == test_users[0]["user_type"]
    assert "password" not in response.json()


@pytest.mark.asyncio
async def test_user_login(async_client: AsyncClient, test_users):
    # Register user first
    await async_client.post(f"{BASE_URL}/auth/register", json=test_users[0])

    # Try logging in
    response = await async_client.post(
        f"{BASE_URL}/auth/login",
        data={  # Use data for form data
            "username": test_users[0]["email"],
            "password": test_users[0]["password"]
        }
    )
    assert response.status_code == status.HTTP_200_OK
    assert "access_token" in response.json()
    assert response.json()["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_invalid_login(async_client: AsyncClient, test_users):
    # Register user first
    await async_client.post(f"{BASE_URL}/auth/register", json=test_users[0])
    # Try logging in with wrong password
    response = await async_client.post(
        f"{BASE_URL}/auth/login",
        data={
            "username": test_users[0]["email"],
            "password": "wrongpassword"
        }
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_password_hashing():
    password = "testpassword123"
    hashed = hash_password(password)
    assert verify_password(password, hashed) == True
    assert verify_password("wrongpassword", hashed) == False


@pytest.mark.asyncio
async def test_protected_route(async_client: AsyncClient, authorized_vendor_client: AsyncClient):
    # Test with valid token
    response = await authorized_vendor_client.get(f"{BASE_URL}/auth/me")
    assert response.status_code == status.HTTP_200_OK

    # Test without token
    response = await async_client.get(f"{BASE_URL}/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_invalid_token(async_client: AsyncClient):
    headers = {"Authorization": "Bearer invalid_token"}
    response = await async_client.get(f"{BASE_URL}/auth/me", headers=headers)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_registration_validation(async_client: AsyncClient):
    # Test invalid email test
    invalid_user = {
        "email": "invalid_email",
        "password": "testpass123",
        "user_type": "VENDOR"
    }
    response = await async_client.post(f"{BASE_URL}/auth/register", json=invalid_user)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Test short password
    invalid_user = {
        "email": "test@example.com",
        "password": "short",
        "user_type": "VENDOR"
    }
    response = await async_client.post(f"{BASE_URL}/auth/register", json=invalid_user)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Test invalid user type
    invalid_user = {
        "email": "test@example.com",
        "password": "testpass123",
        "user_type": "INVALID"
    }
    response = await async_client.post(f"{BASE_URL}/auth/register", json=invalid_user)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
