import pytest
from app.services.auth_service import hash_password, verify_password
from httpx import AsyncClient
from fastapi import status
from typing import Dict
from app.schemas.user_schemas import UserType
import pytest


BASE_URL = "/api"


@pytest.mark.anyio
async def test_user_registration(async_client: AsyncClient):
    """Test user registration with valid data"""
    test_user = {
        "email": "test@example.com",
        "password": "Test@password123",
        "user_type": UserType.CUSTOMER
    }

    response = await async_client.post(
        f"{BASE_URL}/auth/register",
        json=test_user,
        timeout=30.0
    )

    assert response.status_code == status.HTTP_201_CREATED
    # response_data = response.json()
    # assert "email" in response_data
    # assert response_data["email"] == test_user["email"]
    # assert response_data["user_type"] == test_user["user_type"]


@pytest.mark.asyncio
async def test_user_login(async_client: AsyncClient, customer_user_and_token, test_user_data: Dict[str, Dict[str, str]]):
    """Test user login with a pre-existing user."""
    # The customer_user_and_token fixture ensures the user is already created in the DB.
    # We use test_user_data for the login credentials.
    customer_login_data = test_user_data["customer"]

    # Try logging in
    response = await async_client.post(
        f"{BASE_URL}/auth/login",
        data={  # Use data for form data
            "username": customer_login_data["email"],
            "password": customer_login_data["password"],
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert "access_token" in response.json()
    assert response.json()["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_invalid_login(async_client: AsyncClient, customer_user_and_token, test_user_data: Dict[str, Dict[str, str]]):
    """Test invalid login attempt for an existing user."""
    # The customer_user_and_token fixture ensures the user is already created.
    customer_login_data = test_user_data["customer"]

    # Try logging in with wrong password
    response = await async_client.post(
        f"{BASE_URL}/auth/login",
        data={"username": customer_login_data["email"],
              "password": "wrongpassword"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_password_hashing():
    password = "testpassword123"
    hashed = hash_password(password)
    assert verify_password(password, hashed) == True
    assert verify_password("wrongpassword", hashed) == False


@pytest.mark.asyncio
async def test_protected_route(
    async_client: AsyncClient, authorized_customer_client: AsyncClient, test_user_data: Dict[str, Dict[str, str]]
):
    """Test accessing a protected route with and without a valid token."""
    # The authorized_customer_client fixture handles token generation and user creation.
    customer_email = test_user_data["customer"]["email"]

    # Test with valid token
    response = await authorized_customer_client.get(f"{BASE_URL}/auth/me")
    assert response.status_code == status.HTTP_200_OK
    # Verify correct user info
    assert response.json()["email"] == customer_email

    # Test without token
    response = await async_client.get(f"{BASE_URL}/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_invalid_token(async_client: AsyncClient):
    headers = {"Authorization": "Bearer invalid_token"}
    response = await async_client.get(f"{BASE_URL}/auth/me", headers=headers)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_registration_validation(async_client: AsyncClient, test_user_data: Dict[str, Dict[str, str]]):
    # Test invalid email test
    invalid_user = {
        "email": "invalid_email",
        # Use a valid password from test_user_data
        "password": test_user_data["vendor"]["password"],
        "user_type": UserType.VENDOR.value,
    }
    response = await async_client.post(f"{BASE_URL}/auth/register", json=invalid_user)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Test short password
    invalid_user = {
        "email": "newvendor@example.com",  # Use a unique email
        "password": "short",
        "user_type": UserType.VENDOR.value,
    }
    response = await async_client.post(f"{BASE_URL}/auth/register", json=invalid_user)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # Test invalid user type
    invalid_user = {
        "email": "anothernewvendor@example.com",  # Use a unique email
        "password": test_user_data["vendor"]["password"],
        "user_type": "INVALID",
    }
    response = await async_client.post(f"{BASE_URL}/auth/register", json=invalid_user)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
