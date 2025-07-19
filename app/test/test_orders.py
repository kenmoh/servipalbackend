import pytest
from uuid import uuid4
from httpx import AsyncClient
from app.main import app

pytestmark = pytest.mark.anyio


# --- Fixtures ---
@pytest.fixture
def event_loop():
    import asyncio

    loop = asyncio.get_event_loop()
    yield loop


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def test_client():
    return AsyncClient(app=app, base_url="http://test")


@pytest.fixture
def user_data():
    return {
        "email": f"user_{uuid4()}@test.com",
        "password": "TestPassword123!",
        "user_type": "CUSTOMER",
    }


@pytest.fixture
def vendor_data():
    return {
        "email": f"vendor_{uuid4()}@test.com",
        "password": "TestPassword123!",
        "user_type": "RESTAURANT_VENDOR",
    }


@pytest.fixture
def register_and_login(async_client):
    async def _register_and_login(user_data):
        await async_client.post("/api/auth/register", json=user_data)
        login_resp = await async_client.post(
            "/api/auth/login",
            data={"email": user_data["email"], "password": user_data["password"]},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        return token

    return _register_and_login


@pytest.fixture
def create_vendor_item(async_client, register_and_login, vendor_data):
    async def _create_vendor_item():
        vendor_token = await register_and_login(vendor_data)
        headers = {"Authorization": f"Bearer {vendor_token}"}
        item_data = {
            "name": f"Test Food {uuid4()}",
            "description": "Delicious test food",
            "price": 1500,
            "item_type": "food",
            "category_id": None,
            "food_group": "main_course",
        }
        item_resp = await async_client.post(
            "/api/items", json=item_data, headers=headers
        )
        assert item_resp.status_code in (200, 201)
        return item_resp.json(), vendor_token

    return _create_vendor_item


# --- Tests ---
@pytest.mark.asyncio
async def test_order_creation_and_cancel(
    async_client, register_and_login, create_vendor_item, user_data
):
    # Register and login customer
    customer_token = await register_and_login(user_data)
    customer_headers = {"Authorization": f"Bearer {customer_token}"}
    # Create vendor item
    item, vendor_token = await create_vendor_item()
    # Create order
    order_payload = {
        "order_items": [
            {"item_id": item["id"], "quantity": 1, "vendor_id": item["user_id"]}
        ],
        "require_delivery": "delivery",
        "distance": 5.0,
        "origin": "Origin Address",
        "destination": "Destination Address",
        "duration": "30m",
        "pickup_coordinates": [6.45, 3.40],
        "dropoff_coordinates": [6.50, 3.45],
        "additional_info": "No onions",
    }
    order_resp = await async_client.post(
        f"/api/orders/{item['user_id']}", json=order_payload, headers=customer_headers
    )
    assert order_resp.status_code == 201
    order_id = order_resp.json()["order"]["id"]
    # Cancel order with reason
    cancel_reason = "Changed my mind"
    cancel_resp = await async_client.post(
        f"/api/orders/{order_id}/cancel",
        params={"reason": cancel_reason},
        headers=customer_headers,
    )
    assert cancel_resp.status_code == 202
    # Fetch order and check cancel_reason
    get_order_resp = await async_client.get(
        f"/api/orders/{order_id}", headers=customer_headers
    )
    assert get_order_resp.status_code == 200
    assert get_order_resp.json()["order"]["cancel_reason"] == cancel_reason


@pytest.mark.asyncio
async def test_order_reaccept_flow(
    async_client, register_and_login, create_vendor_item, user_data
):
    # Register and login customer
    customer_token = await register_and_login(user_data)
    customer_headers = {"Authorization": f"Bearer {customer_token}"}
    # Create vendor item
    item, vendor_token = await create_vendor_item()
    # Create order
    order_payload = {
        "order_items": [
            {"item_id": item["id"], "quantity": 1, "vendor_id": item["user_id"]}
        ],
        "require_delivery": "delivery",
        "distance": 5.0,
        "origin": "Origin Address",
        "destination": "Destination Address",
        "duration": "30m",
        "pickup_coordinates": [6.45, 3.40],
        "dropoff_coordinates": [6.50, 3.45],
        "additional_info": "No onions",
    }
    order_resp = await async_client.post(
        f"/api/orders/{item['user_id']}", json=order_payload, headers=customer_headers
    )
    assert order_resp.status_code == 201
    order_id = order_resp.json()["order"]["id"]
    # Cancel order
    await async_client.post(
        f"/api/orders/{order_id}/cancel",
        params={"reason": "test"},
        headers=customer_headers,
    )
    # Reaccept order
    reaccept_resp = await async_client.post(
        f"/api/orders/{order_id}/reaccept", headers=customer_headers
    )
    assert reaccept_resp.status_code == 202
    # Fetch order and check status
    get_order_resp = await async_client.get(
        f"/api/orders/{order_id}", headers=customer_headers
    )
    assert get_order_resp.status_code == 200
    assert get_order_resp.json()["order"]["order_status"] == "pending"


@pytest.mark.asyncio
async def test_cancel_permission(
    async_client, register_and_login, create_vendor_item, user_data
):
    # Register and login customer
    customer_token = await register_and_login(user_data)
    customer_headers = {"Authorization": f"Bearer {customer_token}"}
    # Create vendor item
    item, vendor_token = await create_vendor_item()
    # Create order
    order_payload = {
        "order_items": [
            {"item_id": item["id"], "quantity": 1, "vendor_id": item["user_id"]}
        ],
        "require_delivery": "delivery",
        "distance": 5.0,
        "origin": "Origin Address",
        "destination": "Destination Address",
        "duration": "30m",
        "pickup_coordinates": [6.45, 3.40],
        "dropoff_coordinates": [6.50, 3.45],
        "additional_info": "No onions",
    }
    order_resp = await async_client.post(
        f"/api/orders/{item['user_id']}", json=order_payload, headers=customer_headers
    )
    assert order_resp.status_code == 201
    order_id = order_resp.json()["order"]["id"]
    # Register and login another user
    other_user_data = {
        "email": f"other_{uuid4()}@test.com",
        "password": "TestPassword123!",
        "user_type": "CUSTOMER",
    }
    await async_client.post("/api/auth/register", json=other_user_data)
    other_login = await async_client.post(
        "/api/auth/login",
        data={
            "email": other_user_data["email"],
            "password": other_user_data["password"],
        },
    )
    other_token = other_login.json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}
    # Try to cancel as non-owner
    fail_cancel = await async_client.post(
        f"/api/orders/{order_id}/cancel", headers=other_headers
    )
    assert fail_cancel.status_code == 403
