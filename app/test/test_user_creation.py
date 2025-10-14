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
        unique_id = str(uuid.uuid4())[:8]
        payload = {
            "email": f"restaurant_{unique_id}@example.com",
            "password": "Password123!",
            "user_type": UserType.RESTAURANT_VENDOR.value,
            "phone_number": f"+12346{unique_id[:5]}",
        }
        response = await client.post(f"{BASE_URL}/register", json=payload)
        assert response.status_code == 201
        data = response.json()
        print('*' * 70)
        print(data)
        print('*' * 70)
        assert data["email"] == payload["email"]

    # async def test_create_new_customer_user(self, client: AsyncClient):
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"customer_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.CUSTOMER.value,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 201
    #     data = response.json()
    #     assert data["email"] == payload["email"]

    # async def test_create_new_laundry_user(self, client: AsyncClient):
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"laundry_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.LAUNDRY_VENDOR.value,
    #         "phone_number": f"+12347{unique_id[:5]}",
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 201
    #     data = response.json()
    #     assert data["email"] == payload["email"]

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
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": invalid_email,
    #         "password": "Password123!",
    #         "user_type": UserType.CUSTOMER.value,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 422

    # @pytest.mark.parametrize(
    #     "invalid_password",
    #     [
    #         "short",
    #         "nouppercase123!",
    #         "NOLOWERCASE123!",
    #         "NoSpecialChar123",
    #         "NoNumbers!",
    #         "",
    #     ],
    # )
    # async def test_create_user_invalid_password(
    #     self, client: AsyncClient, invalid_password
    # ):
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"test_{unique_id}@example.com",
    #         "password": invalid_password,
    #         "user_type": UserType.CUSTOMER.value,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 422

    # @pytest.mark.parametrize(
    #     "invalid_user_type", ["INVALID_TYPE", "admin1", "user", "", 123]
    # )
    # async def test_create_user_invalid_user_type(
    #     self, client: AsyncClient, invalid_user_type
    # ):
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"test_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": invalid_user_type,
    #         "phone_number": f"+12345{unique_id[:5]}",
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 422

    # async def test_login_user(self, client: AsyncClient):

    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     data = response.json()

    #     login_data = {"username": data['email'], "password": "Password123!"}

    #     response = await client.post(f"{BASE_URL}/login", data=login_data)
        
    #     assert response.status_code == 200
    #     data = response.json()
    #     assert "access_token" in data
    #     assert "refresh_token" in data

    # async def test_login_user_with_wrong_password(self, client: AsyncClient):

    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     data = response.json()

    #     login_data = {"username": data['email'], "password": "Password123"}

    #     response = await client.post(f"{BASE_URL}/login", data=login_data)
        
    #     assert response.status_code == 401
    #     data = response.json()
    #     assert data['detail'] == "Incorrect username or password"

    # async def test_login_user_with_wrong_email(self, client: AsyncClient):

    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #     }
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     data = response.json()

    #     login_data = {"username": "email@gmail.com", "password": "Password123!"}

    #     response = await client.post(f"{BASE_URL}/login", data=login_data)
        
    #     assert response.status_code == 401
    #     data = response.json()
    #     assert data['detail'] == "Incorrect username or password"

    # async def test_create_user_with_existing_email(self, client: AsyncClient):
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #     }
    #     await client.post(f"{BASE_URL}/register", json=payload)
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 400
    #     data = response.json()
    #     assert data["detail"] == "Email already registered"

    # async def test_create_user_with_existing_phone_number(self, client: AsyncClient):
    #     unique_id = str(uuid.uuid4())[:8]
    #     payload = {
    #         "email": f"restaurant_{unique_id}@example.com",
    #         "password": "Password123!",
    #         "user_type": UserType.RESTAURANT_VENDOR.value,
    #         "phone_number": f"+12346{unique_id[:5]}",
    #     }
    #     await client.post(f"{BASE_URL}/register", json=payload)
    #     payload["email"] = f"another_{unique_id}@example.com"
    #     response = await client.post(f"{BASE_URL}/register", json=payload)
    #     assert response.status_code == 400
    #     data = response.json()
    #     assert data["detail"] == "Phone number already registered"
