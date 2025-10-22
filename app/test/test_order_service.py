
import pytest
import uuid
from decimal import Decimal
from httpx import AsyncClient
from app.schemas.status_schema import UserType, OrderStatus, PaymentStatus
from app.schemas.order_schema import OrderType
from app.models.models import Item, User, Profile, Wallet, ChargeAndCommission
from app.services.auth_service import create_new_rider, register_user
from app.services.order_service import create_package_order, order_food_or_request_laundy_service
from app.schemas.user_schemas import CreateUserSchema, RiderCreate
from app.schemas.order_schema import PackageCreate, OrderAndDeliverySchema, OrderItemCreate
from app.schemas.item_schemas import ItemType
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch
from fastapi import UploadFile
import io

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
    user = await register_user(db=async_db, user_data=CreateUserSchema(**user_payload))
    profile = await async_db.get(Profile, user['id'])
    profile.business_name = "Test Restaurant"
    profile.full_name = "Test Vendor"
    await async_db.commit()
    await async_db.refresh(profile)
    user_instance = await async_db.get(User, user['id'])
    return user_instance



@pytest.fixture
async def food_item(async_db: AsyncSession, restaurant_vendor: User) -> Item:
    item = Item(
        name="Test Food Item",
        description="A delicious test food item",
        price=Decimal("12.50"),
        user_id=restaurant_vendor.id,
        item_type=ItemType.FOOD,
    )
    async_db.add(item)
    await async_db.commit()
    await async_db.refresh(item)
    return item

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
            "pickup_coordinates": "6.5244, 3.3792",
            "dropoff_coordinates": "6.5344, 3.3892",
        }
        files = {"image_url": ("test.jpg", io.BytesIO(image_content), "image/jpeg")}
    
        create_package = await async_client.post(f"{BASE_URL}/send-item", data=data, files=files, headers=headers)

        assert create_package.status_code == 201
        response_data = create_package.json()
        assert response_data["order"]["order_type"] == OrderType.PACKAGE.value
        assert response_data["order"]["user_id"] == authenticated_user["user"]["id"]

    async def test_create_food_order_success(self, async_db: AsyncSession, authenticated_restaurant_vendor: User, authenticated_user: User,  create_menu: Item):
        

        order_item_create = OrderItemCreate(
            item_id=food_item.id,
            quantity=2,
            vendor_id=restaurant_vendor.id,
        )
        
        order_and_delivery_schema = OrderAndDeliverySchema(
            order_items=[order_item_create],
            require_delivery="pickup",
            distance=Decimal("3.0"),
            origin="Vendor Address",
            destination="Customer Address",
            duration="10 mins",
            pickup_coordinates=[6.5244, 3.3792],
            dropoff_coordinates=[6.5344, 3.3892],
        )

        

        assert order_response is not None
        assert order_response.order.order_type == OrderType.FOOD
        assert order_response.order.owner_id == customer.id
        assert order_response.order.vendor_id == restaurant_vendor.id
        assert len(order_response.order.order_items) == 1
        assert order_response.order.order_items[0].item_id == food_item.id
