import re
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.status_schema import UserType


BASE_URL = "/api/auth"


class TestUserCreation:
    """Test user creation scenarios."""


    @pytest.mark.asyncio
    async def test_new_customer_user(self, test_client: AsyncClient):
        payload = {
            "email": "customer@example.com",
            "password": "Password123!",
            "user_type": UserType.CUSTOMER,
            "phone_number": "+1234567890"
        }

        response = await test_client.post(f"{BASE_URL}/register", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["user_type"] == UserType.CUSTOMER.value
        assert data["phone_number"] == "+1234567890"
        assert data["email"] == "customer@example.com"

