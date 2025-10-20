import os
import uuid
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient
import pytest
from app.schemas.status_schema import UserType

os.environ["TEST"] = "true"

BASE_URL = "/api/auth"


@pytest.mark.asyncio
class TestUserCreation:
    """Test user creation scenarios."""

    async def test_create_new_restaurant_user(self, client: AsyncClient):
        """Test creating a new restaurant vendor user."""
        unique_id = str(uuid.uuid4())[:8]
        payload = {
            "email": f"restaurant_{unique_id}@example.com",
            "password": "Password123!",
            "user_type": UserType.RESTAURANT_VENDOR,
            "phone_number": f"+12346{unique_id[:5]}",
        }
        response = await client.post(f"{BASE_URL}/register", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == payload["email"]


    # async def test_create_new_customer_user(self, client: AsyncClient):
    #     """Test creating a new customer user."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"customer_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.CUSTOMER.value,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #         "full_name": "Test Customer"
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 201
    #     data = response.json()
    #     assert data["email"] == payload["email"]
    #     assert data["user_type"] == payload["user_type"]
    #     assert "password" not in data

    # async def test_create_new_laundry_user(self, client: AsyncClient):
    #     """Test creating a new laundry vendor user."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"laundry_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.LAUNDRY_VENDOR.value,
    #         "phone_number": f"+12347{unique_id[:5]}",
    #         "full_name": "Test Laundry",
    #         "business_name": "Test Laundry Business"
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 201
    #     data = response.json()
    #     assert data["email"] == payload["email"]
    #     assert data["user_type"] == payload["user_type"]
    #     assert "password" not in data

    # @pytest.mark.parametrize(
    #     "invalid_email",
    #     [
    #         "invalid-email",
    #         "test@",
    #         "@example.com",
    #         "test.example.com",
    #         "",
    #     ],
    # )
    # async def test_create_user_invalid_email(self, client: AsyncClient, invalid_email):
    #     """Test user creation with invalid email formats."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": invalid_email,
    #         "password": "Password123!",
    #         "user_type": UserType.CUSTOMER.value,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #         "full_name": "Test User"
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 422
    #     data = response.json()
    #     assert "email" in str(data["detail"]).lower()

    # @pytest.mark.parametrize(
    #     "invalid_password,expected_error",
    #     [
    #         ("short", "at least 8 characters"),
    #         ("nouppercase123!", "uppercase letter"),
    #         ("NOLOWERCASE123!", "lowercase letter"),
    #         ("NoSpecialChar123", "special character"),
    #         ("NoNumbers!", "number"),
    #         ("", "required"),
    #     ],
    # )
    # async def test_create_user_invalid_password(
    #     self, client: AsyncClient, invalid_password, expected_error
    # ):
    #     """Test user creation with invalid password formats."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"test_{unique_id}@example.com",
    #         "password": invalid_password,
    #         "user_type": UserType.CUSTOMER.value,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #         "full_name": "Test User"
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 422
    #     data = response.json()
    #     assert expected_error.lower() in str(data["detail"]).lower()

    # @pytest.mark.parametrize(
    #     "invalid_user_type", ["INVALID_TYPE", "admin1", "user", "", 123]
    # )
    # async def test_create_user_invalid_user_type(
    #     self, client: AsyncClient, invalid_user_type
    # ):
    #     """Test user creation with invalid user types."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"test_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": invalid_user_type,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #         "full_name": "Test User"
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 422
    #     data = response.json()
    #     assert "user_type" in str(data["detail"]).lower()

    # async def test_login_user(self, client: AsyncClient):
    #     """Test successful user login and token generation."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #         "full_name": "Test Restaurant",
    #         "business_name": "Test Restaurant Business"
    #     }
    #     register_response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert register_response.status_code == 201
    #     user_data = register_response.json()

    #     login_data = {"username": user_data['email'], "password": "Password123!"}
    #     login_response = await client.post(f"{BASE_URL}/login", data=login_data)
        
    #     assert login_response.status_code == 200
    #     auth_data = login_response.json()
    #     assert "access_token" in auth_data
    #     assert "refresh_token" in auth_data
    #     assert "token_type" in auth_data
    #     assert auth_data["token_type"] == "bearer"

    # async def test_login_user_with_wrong_password(self, client: AsyncClient):
    #     """Test login failure with incorrect password."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #         "full_name": "Test Restaurant",
    #         "business_name": "Test Restaurant Business"
    #     }
    #     register_response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert register_response.status_code == 201
    #     user_data = register_response.json()

    #     login_data = {"username": user_data['email'], "password": "WrongPassword123!"}
    #     login_response = await client.post(f"{BASE_URL}/login", data=login_data)
        
    #     assert login_response.status_code == 401
    #     error_data = login_response.json()
    #     assert error_data["detail"] == "Incorrect username or password"

    # async def test_login_user_with_wrong_email(self, client: AsyncClient):
    #     """Test login failure with non-existent email."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     # Create a user first
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #         "full_name": "Test Restaurant",
    #         "business_name": "Test Restaurant Business"
    #     }
    #     register_response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert register_response.status_code == 201

    #     # Try to login with wrong email
    #     login_data = {
    #         "username": f"wrong_{unique_id}@example.com",
    #         "password": "Password123!"
    #     }
    #     login_response = await client.post(f"{BASE_URL}/login", data=login_data)
        
    #     assert login_response.status_code == 401
    #     error_data = login_response.json()
    #     assert error_data["detail"] == "Incorrect username or password"

    # async def test_create_user_with_existing_email(self, client: AsyncClient):
    #     """Test that duplicate email registration is prevented."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #         "full_name": "Test Restaurant",
    #         "business_name": "Test Restaurant Business"
    #     }
    #     # Create first user
    #     first_response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert first_response.status_code == 201
        
    #     # Try to create second user with same email
    #     second_response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert second_response.status_code == 400
    #     error_data = second_response.json()
    #     assert error_data["detail"] == "Email already registered"

    # @pytest.mark.parametrize("invalid_field", [
    #     "email", "password", "user_type", "phone_number", "full_name"
    # ])
    # async def test_create_user_missing_required_fields(self, client: AsyncClient, invalid_field):
    #     """Test user creation with missing required fields."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"test_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.CUSTOMER.value,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #         "full_name": "Test User"
    #     }
    #     del payload[invalid_field]
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 422
    #     data = response.json()
    #     assert invalid_field in str(data["detail"]).lower()

    # async def test_create_user_business_name_required_for_vendors(self, client: AsyncClient):
    #     """Test that business_name is required for vendor user types."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"vendor_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #         "full_name": "Test Vendor"
    #         # Missing business_name
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 422
    #     data = response.json()
    #     assert "business_name" in str(data["detail"]).lower()

    # async def test_create_user_with_existing_phone_number(self, client: AsyncClient):
    #     """Test that duplicate phone number registration is prevented."""
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #         "full_name": "Test Restaurant",
    #         "business_name": "Test Restaurant Business"
    #     }
    #     # Create first user
    #     first_response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert first_response.status_code == 201
        
    #     # Try to create second user with same phone number but different email
    #     payload["email"] = f"another_{unique_id}@example.com"
    #     second_response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert second_response.status_code == 400
    #     error_data = second_response.json()
    #     assert error_data["detail"] == "Phone number already registered"
