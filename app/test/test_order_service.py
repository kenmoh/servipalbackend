
import pytest
import uuid
from decimal import Decimal
from httpx import AsyncClient
from app.schemas.status_schema import UserType, OrderStatus, PaymentStatus, DeliveryStatus
from app.schemas.order_schema import OrderType
from app.models.models import Item, User, Profile, Wallet, ChargeAndCommission, Order,ItemImage, Delivery, OrderItem
from app.services.auth_service import create_new_rider, register_user
from app.services.order_service import create_package_order, order_food_or_request_laundy_service
from app.schemas.user_schemas import CreateUserSchema, RiderCreate
from app.schemas.order_schema import PackageCreate, OrderAndDeliverySchema, OrderItemCreate
from app.schemas.item_schemas import ItemType
from app.schemas.delivery_schemas import CancelOrderSchema
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch
from fastapi import UploadFile
import io
from sqlalchemy import select

BASE_URL = "/api/orders"

@pytest.fixture(autouse=True)
async def seed_charges(async_db: AsyncSession):
    charges = ChargeAndCommission(
        payment_gate_way_fee=Decimal("1.4"),
        value_added_tax=Decimal("7.5"),
        payout_charge_transaction_upto_5000_naira=Decimal("10"),
        payout_charge_transaction_from_5001_to_50_000_naira=Decimal("25"),
        payout_charge_transaction_above_50_000_naira=Decimal("50"),
        stamp_duty=Decimal("50"),
        base_delivery_fee=Decimal("300"),
        delivery_fee_per_km=Decimal("50"),
        delivery_commission_percentage=Decimal("20"),
        food_laundry_commission_percentage=Decimal("15"),
        product_commission_percentage=Decimal("10"),
    )
    async_db.add(charges)
    await async_db.commit()

@pytest.fixture
async def restaurant_vendor(async_db: AsyncSession) -> User:
    user_payload = {
        "email": f"restaurant_{uuid.uuid4().hex[:8]}@example.com",
        "password": "Password123!",
        "user_type": UserType.RESTAURANT_VENDOR,
        "phone_number": f"080{uuid.uuid4().hex[:8]}",
    }
    user = User( **user_payload)
    async_db.add(user)
    await async_db.flush()
    wallet = Wallet(id=user.id, balance=0.0, escrow_balance=0.0)
    async_db.add(wallet)
    profile = Profile(user_id=user.id, business_name="Test Restaurant",  business_address="Test Restaurant Address", business_registration_number="RC1234")
    async_db.add(profile)
    await async_db.commit()
    await async_db.refresh(user)
    return user



@pytest.fixture
async def food_item(async_db: AsyncSession, restaurant_vendor: User) -> Item:
    # Create item first
    item = Item(
        name="Test Food Item",
        description="A delicious test food item",
        price=Decimal("12.50"),
        user_id=restaurant_vendor.id,
        item_type=ItemType.FOOD,
    )
    async_db.add(item)
    await async_db.flush()  # Flush to get the item ID
    
    # Create ItemImage for the food item
    item_image = ItemImage(
        item_id=item.id,
        url="https://example.com/test-food-image.jpg",
        is_primary=True
    )
    async_db.add(item_image)
    await async_db.commit()
    await async_db.refresh(item)
    return item

@pytest.fixture
async def laundry_vendor(async_db: AsyncSession) -> User:
    user_payload = {
        "email": f"laundry_{uuid.uuid4().hex[:8]}@example.com",
        "password": "Password123!",
        "user_type": UserType.LAUNDRY_VENDOR,
        "phone_number": f"080{uuid.uuid4().hex[:8]}",
    }
    user = await User(**user_payload)
    async_db.add(user)
    await async_db.flush()
    wallet = Wallet(id=user.id, balance=0.0, escrow_balance=0.0)
    async_db.add(wallet)
    profile = Profile(user_id=user.id, business_name="Test Laundry", business_address="Test Laundry Address", business_registration_number="RC1234")
    async_db.add(profile)
    await async_db.commit()
    await async_db.refresh(user)
    return user

@pytest.fixture
async def laundry_item(async_db: AsyncSession, laundry_vendor: User) -> Item:
    # Create item first
    item = Item(
        name="Test Laundry Item",
        description="A test laundry service",
        price=Decimal("25.00"),
        user_id=laundry_vendor.id,
        item_type=ItemType.LAUNDRY,
    )
    async_db.add(item)
    await async_db.flush()  # Flush to get the item ID
    
    # Create ItemImage for the laundry item
    item_image = ItemImage(
        item_id=item.id,
        url="https://example.com/test-laundry-image.jpg",
        is_primary=True
    )
    async_db.add(item_image)
    await async_db.commit()
    await async_db.refresh(item)
    return item

@pytest.fixture
async def authenticated_laundry_vendor(async_client: AsyncClient, laundry_vendor: User):
    """Create authenticated laundry vendor for testing."""
    login_data = {"username": laundry_vendor.email, "password": "Password123!"}
    login_response = await async_client.post("/api/auth/login", data=login_data)
    assert login_response.status_code == 200
    auth_data = login_response.json()
    
    return {
        "user": laundry_vendor,
        "access_token": auth_data["access_token"],
        "headers": {"Authorization": f"Bearer {auth_data['access_token']}"},
    }

@pytest.mark.asyncio
class TestOrderService:

    async def test_create_package_order_success(self, async_client: AsyncClient, create_charge_and_commission, authenticated_user):
    
        # Create a dummy file
        image_content = b"fake image data"
        headers = {"Authorization": f"Bearer {authenticated_user['access_token']}"}
        data = {
            "name": "Test Package",
            "description": "A test package",
            "distance": "5.0",
            "origin": "Origin Address",
            "destination": "Destination Address",
            "duration": "15 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        files = {"image_url": ("test.jpg", io.BytesIO(image_content), "image/jpeg")}
    
        create_package = await async_client.post(f"{BASE_URL}/send-item", data=data, files=files, headers=headers)

        assert create_package.status_code == 201
        response_data = create_package.json()
        assert response_data["order"]["order_type"] == OrderType.PACKAGE.value
        assert response_data["order"]["user_id"] == authenticated_user["user"]["id"]

    async def test_create_food_order_success(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_restaurant_vendor, authenticated_user, food_item):
        """Test successful food order creation."""
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 2,
                    "vendor_id": str(authenticated_restaurant_vendor["user"]["id"])
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{authenticated_restaurant_vendor['user']['id']}",
            json=order_data,
            headers=headers
        )
        
        assert response.status_code == 201
        response_data = response.json()
        assert response_data["order"]["order_type"] == OrderType.FOOD.value
        assert response_data["order"]["owner_id"] == authenticated_user["user"]["id"]
        assert response_data["order"]["vendor_id"] == authenticated_restaurant_vendor["user"]["id"]

    async def test_create_laundry_order_success(self, async_client: AsyncClient, authenticated_laundry_vendor, authenticated_user, laundry_item):
        """Test successful laundry order creation."""
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(laundry_item.id),
                    "quantity": 1,
                    "vendor_id": str(authenticated_laundry_vendor["user"]["id"])
                }
            ],
            "require_delivery": "pickup",
            "distance": 5.0,
            "origin": "Customer Address",
            "destination": "Laundry Vendor Address",
            "duration": "15 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{authenticated_laundry_vendor['user']['id']}",
            json=order_data,
            headers=headers
        )
        
        assert response.status_code == 201
        response_data = response.json()
        assert response_data["order"]["order_type"] == OrderType.LAUNDRY.value
        assert response_data["order"]["owner_id"] == authenticated_user["user"]["id"]
        assert response_data["order"]["vendor_id"] == authenticated_laundry_vendor["user"]["id"]

    async def test_create_package_order_invalid_coordinates(self, async_client: AsyncClient, authenticated_user):
        """Test package order creation with invalid coordinates."""
        headers = authenticated_user["headers"]
        image_content = b"fake image data"
        data = {
            "name": "Test Package",
            "description": "A test package",
            "distance": "5.0",
            "origin": "Origin Address",
            "destination": "Destination Address",
            "duration": "15 mins",
            "pickup_coordinates": "invalid_coordinates",
            "dropoff_coordinates": "6.5344, 3.3892",
        }
        files = {"image_url": ("test.jpg", io.BytesIO(image_content), "image/jpeg")}
        
        response = await async_client.post(f"{BASE_URL}/send-item", data=data, files=files, headers=headers)
        assert response.status_code == 422

    async def test_create_package_order_missing_image(self, async_client: AsyncClient, authenticated_user):
        """Test package order creation without image."""
        headers = authenticated_user["headers"]
        data = {
            "name": "Test Package",
            "description": "A test package",
            "distance": "5.0",
            "origin": "Origin Address",
            "destination": "Destination Address",
            "duration": "15 mins",
            "pickup_coordinates": "6.5244, 3.3792",
            "dropoff_coordinates": "6.5344, 3.3892",
        }
        
        response = await async_client.post(f"{BASE_URL}/send-item", data=data, headers=headers)
        assert response.status_code == 422

    async def test_create_food_order_self_order_prevention(self, async_client: AsyncClient, authenticated_restaurant_vendor, food_item):
        """Test that vendors cannot order their own items."""
        headers = authenticated_restaurant_vendor["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 2,
                    "vendor_id": str(authenticated_restaurant_vendor["user"]["id"])
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{authenticated_restaurant_vendor['user']['id']}",
            json=order_data,
            headers=headers
        )
        
        assert response.status_code == 403
        assert "cannot order your own item" in response.json()["detail"]

    async def test_create_food_order_mixed_vendor_items(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, restaurant_vendor, food_item):
        """Test that order items must belong to the same vendor."""
        # Create another vendor and item
        other_vendor_payload = {
            "email": f"other_restaurant_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Password123!",
            "user_type": UserType.RESTAURANT_VENDOR,
            "phone_number": f"080{uuid.uuid4().hex[:8]}",
        }
        other_vendor = await register_user(db=async_db, user_data=CreateUserSchema(**other_vendor_payload))
        other_profile = await async_db.get(Profile, other_vendor['id'])
        other_profile.business_name = "Other Restaurant"
        await async_db.commit()
        
        other_item = Item(
            name="Other Food Item",
            description="Another food item",
            price=Decimal("15.00"),
            user_id=other_vendor['id'],
            item_type=ItemType.FOOD,
        )
        async_db.add(other_item)
        await async_db.commit()
        await async_db.refresh(other_item)
        
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                },
                {
                    "item_id": str(other_item.id),
                    "quantity": 1,
                    "vendor_id": str(other_vendor['id'])
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        
        assert response.status_code == 403
        assert "must belong to the same vendor" in response.json()["detail"]

    async def test_create_order_insufficient_profile_info(self, async_client: AsyncClient, async_db: AsyncSession, food_item):
        """Test order creation with insufficient profile information."""
        # Create user without complete profile
        user_payload = {
            "email": f"incomplete_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Password123!",
            "user_type": UserType.CUSTOMER,
            "phone_number": f"080{uuid.uuid4().hex[:8]}",
        }
        user = await register_user(db=async_db, user_data=CreateUserSchema(**user_payload))
        
        # Login the user
        login_data = {"username": user_payload["email"], "password": user_payload["password"]}
        login_response = await async_client.post("/api/auth/login", data=login_data)
        auth_data = login_response.json()
        headers = {"Authorization": f"Bearer {auth_data['access_token']}"}
        
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(food_item.user_id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{food_item.user_id}",
            json=order_data,
            headers=headers
        )
        
        assert response.status_code == 400
        assert "Phone number and full name are required" in response.json()["detail"]

    async def test_get_order_by_id_success(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, food_item, restaurant_vendor):
        """Test successful order retrieval by ID."""
        # First create an order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Now retrieve the order
        response = await async_client.get(f"{BASE_URL}/{order_id}")
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["order"]["id"] == order_id
        assert response_data["order"]["order_type"] == OrderType.FOOD.value

    async def test_get_order_by_id_not_found(self, async_client: AsyncClient):
        """Test order retrieval with non-existent ID."""
        fake_order_id = str(uuid.uuid4())
        response = await async_client.get(f"{BASE_URL}/{fake_order_id}")
        assert response.status_code == 404

    async def test_get_all_delivery_orders(self, async_client: AsyncClient, async_db: AsyncSession):
        """Test retrieval of all delivery orders."""
        response = await async_client.get(f"{BASE_URL}/delivery-orders")
        assert response.status_code == 200
        response_data = response.json()
        assert "orders" in response_data
        assert "total" in response_data
        assert "page" in response_data
        assert "limit" in response_data

    async def test_get_all_delivery_orders_with_pagination(self, async_client: AsyncClient):
        """Test delivery orders retrieval with pagination."""
        response = await async_client.get(f"{BASE_URL}/delivery-orders?skip=0&limit=10")
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["limit"] == 10
        assert response_data["page"] == 0

    async def test_customer_confirm_order_received(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, food_item, restaurant_vendor):
        """Test customer confirming order received."""
        # Create an order first
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Update order status to delivered first (simulate delivery completion)
        order = await async_db.get(Order, order_id)
        order.order_status = OrderStatus.DELIVERED
        await async_db.commit()
        
        # Customer confirms order received
        response = await async_client.put(
            f"{BASE_URL}/{order_id}/customer-confirm-order-received",
            headers=headers
        )
        assert response.status_code == 202
        response_data = response.json()
        assert response_data["order_status"] == OrderStatus.COMPLETED.value

    async def test_vendor_mark_order_delivered(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_restaurant_vendor, food_item):
        """Test vendor marking order as delivered."""
        # Create an order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(authenticated_restaurant_vendor["user"]["id"])
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{authenticated_restaurant_vendor['user']['id']}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Update order status to paid first
        order = await async_db.get(Order, order_id)
        order.order_payment_status = PaymentStatus.PAID
        order.order_status = OrderStatus.CONFIRMED
        await async_db.commit()
        
        # Vendor marks order as delivered
        vendor_headers = authenticated_restaurant_vendor["headers"]
        response = await async_client.put(
            f"{BASE_URL}/{order_id}/vendor-mark-order-delivered",
            headers=vendor_headers
        )
        assert response.status_code == 202
        response_data = response.json()
        assert response_data["order_status"] == OrderStatus.DELIVERED.value

    async def test_cancel_order_success(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, food_item, restaurant_vendor):
        """Test successful order cancellation."""
        # Create an order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Cancel the order
        response = await async_client.put(
            f"{BASE_URL}/{order_id}/cancel",
            json={"reason": "Changed my mind"},
            headers=headers
        )
        assert response.status_code == 202
        response_data = response.json()
        assert response_data["order_status"] == OrderStatus.CANCELLED.value

    async def test_generate_new_payment_link(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, food_item, restaurant_vendor):
        """Test generating new payment link for an order."""
        # Create an order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Generate new payment link
        with patch('app.utils.utils.get_payment_link', return_value="https://example.com/payment"):
            response = await async_client.put(
                f"{BASE_URL}/{order_id}/generate-new-payment-link",
                headers=headers
            )
            assert response.status_code == 202
            response_data = response.json()
            assert "payment_link" in response_data

    async def test_unauthorized_access_to_order_operations(self, async_client: AsyncClient, async_db: AsyncSession, food_item, restaurant_vendor):
        """Test unauthorized access to order operations."""
        # Create another user
        user_payload = {
            "email": f"unauthorized_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Password123!",
            "user_type": UserType.CUSTOMER,
            "phone_number": f"080{uuid.uuid4().hex[:8]}",
        }
        user = await register_user(db=async_db, user_data=CreateUserSchema(**user_payload))
        
        # Login the user
        login_data = {"username": user_payload["email"], "password": user_payload["password"]}
        login_response = await async_client.post("/api/auth/login", data=login_data)
        auth_data = login_response.json()
        headers = {"Authorization": f"Bearer {auth_data['access_token']}"}
        
        # Try to access order operations without proper authorization
        fake_order_id = str(uuid.uuid4())
        response = await async_client.put(
            f"{BASE_URL}/{fake_order_id}/customer-confirm-order-received",
            headers=headers
        )
        # Should return 404 for non-existent order or 403 for unauthorized access
        assert response.status_code in [403, 404]

    async def test_create_order_with_invalid_item_id(self, async_client: AsyncClient, authenticated_user, restaurant_vendor):
        """Test order creation with invalid item ID."""
        headers = authenticated_user["headers"]
        fake_item_id = str(uuid.uuid4())
        order_data = {
            "order_items": [
                {
                    "item_id": fake_item_id,
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert response.status_code == 400
        assert "Item not found" in response.json()["detail"]

    async def test_create_order_with_zero_quantity(self, async_client: AsyncClient, authenticated_user, food_item, restaurant_vendor):
        """Test order creation with zero quantity."""
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 0,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert response.status_code == 422  # Validation error for zero quantity

    async def test_create_order_with_negative_quantity(self, async_client: AsyncClient, authenticated_user, food_item, restaurant_vendor):
        """Test order creation with negative quantity."""
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": -1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert response.status_code == 422  # Validation error for negative quantity

    async def test_rider_accept_delivery(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_rider, food_item, restaurant_vendor):
        """Test rider accepting a delivery order."""
        # Create an order with delivery
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "vendor_pickup_and_dropoff",
            "distance": 5.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "15 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Update order status to paid and confirmed
        order = await async_db.get(Order, order_id)
        order.order_payment_status = PaymentStatus.PAID
        order.order_status = OrderStatus.CONFIRMED
        await async_db.commit()
        
        # Rider accepts delivery
        rider_headers = authenticated_rider["headers"]
        response = await async_client.put(
            f"{BASE_URL}/{order_id}/accept-delivery",
            headers=rider_headers
        )
        assert response.status_code == 202
        response_data = response.json()
        assert response_data["delivery_status"] == DeliveryStatus.ACCEPTED.value

    async def test_laundry_pickup_workflow(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_rider, laundry_item, laundry_vendor):
        """Test laundry pickup workflow."""
        # Create a laundry order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(laundry_item.id),
                    "quantity": 1,
                    "vendor_id": str(laundry_vendor.id)
                }
            ],
            "require_delivery": "vendor_pickup_and_dropoff",
            "distance": 3.0,
            "origin": "Customer Address",
            "destination": "Laundry Vendor Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{laundry_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Update order status to paid and confirmed
        order = await async_db.get(Order, order_id)
        order.order_payment_status = PaymentStatus.PAID
        order.order_status = OrderStatus.CONFIRMED
        await async_db.commit()
        
        # Rider accepts delivery first
        rider_headers = authenticated_rider["headers"]
        accept_response = await async_client.put(
            f"{BASE_URL}/{order_id}/accept-delivery",
            headers=rider_headers
        )
        assert accept_response.status_code == 202
        
        # Rider picks up laundry
        pickup_response = await async_client.put(
            f"{BASE_URL}/{order_id}/pickup-laundry",
            headers=rider_headers
        )
        assert pickup_response.status_code == 202
        response_data = pickup_response.json()
        assert response_data["delivery_status"] == DeliveryStatus.PICKED_UP.value

    async def test_laundry_returned_workflow(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_rider, laundry_item, laundry_vendor):
        """Test laundry returned workflow."""
        # Create a laundry order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(laundry_item.id),
                    "quantity": 1,
                    "vendor_id": str(laundry_vendor.id)
                }
            ],
            "require_delivery": "vendor_pickup_and_dropoff",
            "distance": 3.0,
            "origin": "Customer Address",
            "destination": "Laundry Vendor Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{laundry_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Update order status to paid and confirmed
        order = await async_db.get(Order, order_id)
        order.order_payment_status = PaymentStatus.PAID
        order.order_status = OrderStatus.CONFIRMED
        await async_db.commit()
        
        # Rider accepts delivery and picks up laundry
        rider_headers = authenticated_rider["headers"]
        await async_client.put(f"{BASE_URL}/{order_id}/accept-delivery", headers=rider_headers)
        await async_client.put(f"{BASE_URL}/{order_id}/pickup-laundry", headers=rider_headers)
        
        # Rider returns laundry
        return_response = await async_client.put(
            f"{BASE_URL}/{order_id}/laundry-returned",
            headers=rider_headers
        )
        assert return_response.status_code == 202
        response_data = return_response.json()
        assert response_data["delivery_status"] == DeliveryStatus.RETURNED.value

    async def test_package_delivered_workflow(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_rider):
        """Test package delivered workflow."""
        # Create a package order
        headers = authenticated_user["headers"]
        image_content = b"fake image data"
        data = {
            "name": "Test Package",
            "description": "A test package",
            "distance": "5.0",
            "origin": "Origin Address",
            "destination": "Destination Address",
            "duration": "15 mins",
            "pickup_coordinates": "6.5244, 3.3792",
            "dropoff_coordinates": "6.5344, 3.3892",
        }
        files = {"image_url": ("test.jpg", io.BytesIO(image_content), "image/jpeg")}
        
        create_response = await async_client.post(f"{BASE_URL}/send-item", data=data, files=files, headers=headers)
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        delivery_id = create_response.json()["delivery"]["id"]
        
        # Update order status to paid and confirmed
        order = await async_db.get(Order, order_id)
        order.order_payment_status = PaymentStatus.PAID
        order.order_status = OrderStatus.CONFIRMED
        await async_db.commit()
        
        # Rider accepts delivery first
        rider_headers = authenticated_rider["headers"]
        accept_response = await async_client.put(
            f"{BASE_URL}/{order_id}/accept-delivery",
            headers=rider_headers
        )
        assert accept_response.status_code == 202
        
        # Rider marks package as delivered
        delivered_response = await async_client.put(
            f"{BASE_URL}/{delivery_id}/package-delivered",
            headers=rider_headers
        )
        assert delivered_response.status_code == 202
        response_data = delivered_response.json()
        assert response_data["delivery_status"] == DeliveryStatus.DELIVERED.value

    async def test_laundry_vendor_mark_item_received(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_laundry_vendor, laundry_item):
        """Test laundry vendor marking item as received."""
        # Create a laundry order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(laundry_item.id),
                    "quantity": 1,
                    "vendor_id": str(authenticated_laundry_vendor["user"]["id"])
                }
            ],
            "require_delivery": "vendor_pickup_and_dropoff",
            "distance": 3.0,
            "origin": "Customer Address",
            "destination": "Laundry Vendor Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{authenticated_laundry_vendor['user']['id']}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        delivery_id = create_response.json()["delivery"]["id"]
        
        # Update order status to paid and confirmed
        order = await async_db.get(Order, order_id)
        order.order_payment_status = PaymentStatus.PAID
        order.order_status = OrderStatus.CONFIRMED
        await async_db.commit()
        
        # Laundry vendor marks item as received
        vendor_headers = authenticated_laundry_vendor["headers"]
        response = await async_client.put(
            f"{BASE_URL}/{delivery_id}/laundry-received",
            headers=vendor_headers
        )
        assert response.status_code == 202
        response_data = response.json()
        assert response_data["delivery_status"] == DeliveryStatus.RECEIVED.value

    async def test_re_list_item_for_delivery(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_rider):
        """Test re-listing item for delivery."""
        # Create a package order
        headers = authenticated_user["headers"]
        image_content = b"fake image data"
        data = {
            "name": "Test Package",
            "description": "A test package",
            "distance": "5.0",
            "origin": "Origin Address",
            "destination": "Destination Address",
            "duration": "15 mins",
            "pickup_coordinates": "6.5244, 3.3792",
            "dropoff_coordinates": "6.5344, 3.3892",
        }
        files = {"image_url": ("test.jpg", io.BytesIO(image_content), "image/jpeg")}
        
        create_response = await async_client.post(f"{BASE_URL}/send-item", data=data, files=files, headers=headers)
        assert create_response.status_code == 201
        delivery_id = create_response.json()["delivery"]["id"]
        
        # Re-list item for delivery
        rider_headers = authenticated_rider["headers"]
        response = await async_client.put(
            f"{BASE_URL}/{delivery_id}/re-list-item",
            headers=rider_headers
        )
        assert response.status_code == 202
        response_data = response.json()
        assert response_data["delivery_status"] == DeliveryStatus.AVAILABLE.value

    async def test_cancel_delivery_with_reason(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_rider):
        """Test canceling delivery with reason."""
        # Create a package order
        headers = authenticated_user["headers"]
        image_content = b"fake image data"
        data = {
            "name": "Test Package",
            "description": "A test package",
            "distance": "5.0",
            "origin": "Origin Address",
            "destination": "Destination Address",
            "duration": "15 mins",
            "pickup_coordinates": "6.5244, 3.3792",
            "dropoff_coordinates": "6.5344, 3.3892",
        }
        files = {"image_url": ("test.jpg", io.BytesIO(image_content), "image/jpeg")}
        
        create_response = await async_client.post(f"{BASE_URL}/send-item", data=data, files=files, headers=headers)
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Cancel delivery with reason
        cancel_data = {"reason": "Customer requested cancellation"}
        response = await async_client.put(
            f"{BASE_URL}/{order_id}/cancel-delivery",
            json=cancel_data,
            headers=headers
        )
        assert response.status_code == 202
        response_data = response.json()
        assert response_data["delivery_status"] == DeliveryStatus.CANCELLED.value

    async def test_admin_modify_order_status(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_dispatch_admin, food_item, restaurant_vendor):
        """Test admin modifying order status."""
        # Create an order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Admin modifies order status
        admin_headers = {"Authorization": f"Bearer {authenticated_dispatch_admin['access_token']}"}
        response = await async_client.put(
            f"{BASE_URL}/{order_id}/admin-modify-status",
            params={"new_order_status": OrderStatus.CONFIRMED.value},
            headers=admin_headers
        )
        assert response.status_code == 202
        response_data = response.json()
        assert response_data["order_status"] == OrderStatus.CONFIRMED.value

    async def test_get_user_related_orders(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, food_item, restaurant_vendor):
        """Test getting user-related orders."""
        # Create an order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        
        # Get user-related orders
        user_id = authenticated_user["user"]["id"]
        response = await async_client.get(f"{BASE_URL}/{user_id}/user-related-orders")
        assert response.status_code == 200
        response_data = response.json()
        assert isinstance(response_data, list)
        assert len(response_data) >= 1

    async def test_get_paid_pending_deliveries(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, authenticated_rider, food_item, restaurant_vendor):
        """Test getting paid pending deliveries for rider."""
        # Create an order with delivery
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "vendor_pickup_and_dropoff",
            "distance": 5.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "15 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Update order status to paid
        order = await async_db.get(Order, order_id)
        order.order_payment_status = PaymentStatus.PAID
        await async_db.commit()
        
        # Get paid pending deliveries
        rider_headers = authenticated_rider["headers"]
        response = await async_client.get(
            f"{BASE_URL}/paid-pending-deliveries",
            headers=rider_headers
        )
        assert response.status_code == 200
        response_data = response.json()
        assert isinstance(response_data, list)

    async def test_create_review_for_order(self, async_client: AsyncClient, async_db: AsyncSession, authenticated_user, food_item, restaurant_vendor):
        """Test creating a review for an order."""
        # Create an order
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892]
        }
        
        create_response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        assert create_response.status_code == 201
        order_id = create_response.json()["order"]["id"]
        
        # Update order status to completed
        order = await async_db.get(Order, order_id)
        order.order_status = OrderStatus.COMPLETED
        await async_db.commit()
        
        # Create review
        review_data = {
            "rating": 5,
            "comment": "Excellent service!"
        }
        response = await async_client.post(
            f"{BASE_URL}/{order_id}/review",
            json=review_data,
            headers=headers
        )
        assert response.status_code == 201
        response_data = response.json()
        assert response_data["rating"] == 5
        assert response_data["comment"] == "Excellent service!"

    async def test_create_order_with_delivery_requirements(self, async_client: AsyncClient, authenticated_user, food_item, restaurant_vendor):
        """Test creating order with delivery requirements."""
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 2,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "vendor_pickup_and_dropoff",
            "distance": 10.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "30 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892],
            "is_one_way_delivery": True
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        
        assert response.status_code == 201
        response_data = response.json()
        assert response_data["order"]["require_delivery"] == "vendor_pickup_and_dropoff"
        assert response_data["delivery"] is not None
        assert response_data["delivery"]["distance"] == 10.0

    async def test_create_order_with_additional_info(self, async_client: AsyncClient, authenticated_user, food_item, restaurant_vendor):
        """Test creating order with additional information."""
        headers = authenticated_user["headers"]
        order_data = {
            "order_items": [
                {
                    "item_id": str(food_item.id),
                    "quantity": 1,
                    "vendor_id": str(restaurant_vendor.id)
                }
            ],
            "require_delivery": "pickup",
            "distance": 3.0,
            "origin": "Vendor Address",
            "destination": "Customer Address",
            "duration": "10 mins",
            "pickup_coordinates": [6.5244, 3.3792],
            "dropoff_coordinates": [6.5344, 3.3892],
            "additional_info": "Please make it extra spicy"
        }
        
        response = await async_client.post(
            f"{BASE_URL}/{restaurant_vendor.id}",
            json=order_data,
            headers=headers
        )
        
        assert response.status_code == 201
        response_data = response.json()
        assert response_data["order"]["additional_info"] == "Please make it extra spicy"
