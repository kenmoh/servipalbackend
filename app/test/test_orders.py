"""Test order service endpoints with comprehensive scenarios."""
import pytest
import uuid
from decimal import Decimal
from httpx import AsyncClient

from app.schemas.status_schema import UserType, OrderStatus
from app.schemas.order_schema import OrderType
from app.models.models import Item, User
from app.services.auth_service import create_user

BASE_URL = "/api/orders"

@pytest.fixture
async def test_vendor(session, client):
    """Create a test vendor for orders."""
    unique_id = str(uuid.uuid4())[:8]
    user_data = {
        "email": f"vendor_{unique_id}@example.com",
        "password": "Password123!",
        "user_type": UserType.RESTAURANT_VENDOR.value,
        "phone_number": f"+12345{unique_id[:5]}",
        "full_name": "Test Vendor",
        "business_name": "Test Restaurant"
    }
    response = await client.post("/api/auth/register", json=user_data)
    assert response.status_code == 201
    return response.json()

@pytest.fixture
async def test_customer(session, client):
    """Create a test customer for orders."""
    unique_id = str(uuid.uuid4())[:8]
    user_data = {
        "email": f"customer_{unique_id}@example.com",
        "password": "Password123!",
        "user_type": UserType.CUSTOMER.value,
        "phone_number": f"+12346{unique_id[:5]}",
        "full_name": "Test Customer"
    }
    response = await client.post("/api/auth/register", json=user_data)
    assert response.status_code == 201
    return response.json()

@pytest.fixture
async def test_menu_item(session, test_vendor):
    """Create a test menu item for orders."""
    item = Item(
        name="Test Item",
        description="Test item description",
        price=Decimal("10.99"),
        vendor_id=test_vendor["id"],
        available=True,
        category="Food"
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item

@pytest.fixture
async def auth_headers(client, test_customer):
    """Get auth headers for customer."""
    login_data = {"username": test_customer["email"], "password": "Password123!"}
    auth_response = await client.post("/api/auth/login", data=login_data)
    assert auth_response.status_code == 200
    token = auth_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
async def vendor_headers(client, test_vendor):
    """Get auth headers for vendor."""
    login_data = {"username": test_vendor["email"], "password": "Password123!"}
    auth_response = await client.post("/api/auth/login", data=login_data)
    assert auth_response.status_code == 200
    token = auth_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

class TestOrderCreation:
    """Test order creation scenarios."""

    @pytest.mark.asyncio
    async def test_create_food_order_success(
        self, client, test_vendor, test_menu_item, auth_headers
    ):
        """Test successful food order creation."""
        order_payload = {
            "vendor_id": test_vendor["id"],
            "order_items": [{
                "item_id": str(test_menu_item.id),
                "quantity": 2
            }],
            "require_delivery": "delivery",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892],
        }
        
        response = await client.post(
            f"{BASE_URL}/food-laundry",
            json=order_payload,
            headers=auth_headers
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == OrderStatus.PENDING.value
        assert data["total_amount"] == str(test_menu_item.price * 2)
        assert data["vendor_id"] == test_vendor["id"]
        assert len(data["order_items"]) == 1
        assert data["order_items"][0]["quantity"] == 2

    @pytest.mark.parametrize("invalid_data,expected_error", [
        (
            {"vendor_id": "invalid-uuid", "order_items": []},
            "Invalid vendor_id format"
        ),
        (
            {"vendor_id": str(uuid.uuid4()), "order_items": []},
            "order_items cannot be empty"
        ),
        (
            {"order_items": [{"item_id": str(uuid.uuid4()), "quantity": 0}]},
            "quantity must be greater than 0"
        ),
    ])
    @pytest.mark.asyncio
    async def test_create_order_invalid_data(
        self, client, auth_headers, invalid_data, expected_error
    ):
        """Test order creation with invalid data."""
        response = await client.post(
            f"{BASE_URL}/food-laundry",
            json=invalid_data,
            headers=auth_headers
        )
        assert response.status_code == 422
        error_detail = str(response.json()["detail"]).lower()
        assert expected_error.lower() in error_detail

class TestOrderManagement:
    """Test order management operations."""

    @pytest.mark.asyncio
    async def test_complete_order_flow(
        self, client, test_vendor, test_menu_item, auth_headers, vendor_headers
    ):
        """Test complete order flow from creation to completion."""
        # Create order
        order_payload = {
            "vendor_id": test_vendor["id"],
            "order_items": [{
                "item_id": str(test_menu_item.id),
                "quantity": 1
            }],
            "require_delivery": "delivery",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await client.post(
            f"{BASE_URL}/food-laundry",
            json=order_payload,
            headers=auth_headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["id"]

        # Vendor accepts order
        accept_response = await client.put(
            f"{BASE_URL}/{order_id}/accept",
            headers=vendor_headers
        )
        assert accept_response.status_code == 200
        assert accept_response.json()["status"] == OrderStatus.ACCEPTED.value

        # Vendor marks order as ready
        ready_response = await client.put(
            f"{BASE_URL}/{order_id}/ready",
            headers=vendor_headers
        )
        assert ready_response.status_code == 200
        assert ready_response.json()["status"] == OrderStatus.READY.value

    @pytest.mark.asyncio
    async def test_order_cancellation(
        self, client, test_vendor, test_menu_item, auth_headers
    ):
        """Test order cancellation by customer."""
        # Create order
        order_payload = {
            "vendor_id": test_vendor["id"],
            "order_items": [{
                "item_id": str(test_menu_item.id),
                "quantity": 1
            }],
            "require_delivery": "delivery",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await client.post(
            f"{BASE_URL}/food-laundry",
            json=order_payload,
            headers=auth_headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["id"]

        # Cancel order
        cancel_payload = {"reason": "Changed my mind"}
        cancel_response = await client.put(
            f"{BASE_URL}/{order_id}/cancel",
            json=cancel_payload,
            headers=auth_headers
        )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == OrderStatus.CANCELLED.value