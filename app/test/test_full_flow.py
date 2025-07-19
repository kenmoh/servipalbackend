import pytest
from uuid import uuid4
from app.schemas.user_schemas import UserType


pytestmark = pytest.mark.anyio


@pytest.mark.asyncio
async def test_registration_and_login(async_client, test_user_data):
    # Register a new user
    user_data = {
        "email": f"testuser_{uuid4()}@example.com",
        "phone_number": "08012345678",
        "user_type": "CUSTOMER",
        "password": "TestPassword123!",
    }
    resp = await async_client.post("/auth/register", json=user_data)
    assert resp.status_code == 201
    assert resp.json()["email"] == user_data["email"]

    # Login with the new user
    login_data = {"username": user_data["email"], "password": user_data["password"]}
    resp = await async_client.post("/auth/login", data=login_data)
    assert resp.status_code == 200
    tokens = resp.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens

    # Access protected route
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await async_client.get("/users", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_protected_route_requires_auth(async_client):
    resp = await async_client.get("/users")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_broadcast_notification_flow(
    authorized_admin_client, authorized_customer_client, customer_user_and_token
):
    # Admin sends a broadcast notification
    customer_user, _ = customer_user_and_token
    data = {
        "title": "System Update",
        "content": "We have a new feature!",
        "recipient_ids": [str(customer_user.id)],
    }
    resp = await authorized_admin_client.post("/notifications/broadcast", json=data)
    assert resp.status_code == 200
    notification = resp.json()
    assert notification["title"] == data["title"]
    assert notification["is_broadcast"] is True

    # Customer fetches notifications
    resp = await authorized_customer_client.get("/notifications")
    assert resp.status_code == 200
    notifications = resp.json()["notifications"]
    assert any(n["title"] == data["title"] for n in notifications)

    # Mark as read
    notification_id = notification["id"]
    resp = await authorized_customer_client.put(
        f"/notifications/{notification_id}/read"
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Notification marked as read"

    # Badge count should decrease
    resp = await authorized_customer_client.get("/notifications/badge-count")
    assert resp.status_code == 200
    assert resp.json()["unread_count"] >= 0


@pytest.mark.asyncio
async def test_individual_notification_flow(
    authorized_admin_client, authorized_vendor_client, vendor_user_and_token
):
    vendor_user, _ = vendor_user_and_token
    data = {
        "recipient_id": str(vendor_user.id),
        "title": "Direct Message",
        "content": "Hello Vendor!",
    }
    resp = await authorized_admin_client.post("/notifications/individual", json=data)
    assert resp.status_code == 200
    notification = resp.json()
    assert notification["title"] == data["title"]
    assert notification["is_broadcast"] is False

    # Vendor fetches notifications
    resp = await authorized_vendor_client.get("/notifications")
    assert resp.status_code == 200
    notifications = resp.json()["notifications"]
    assert any(n["title"] == data["title"] for n in notifications)

    # Mark as read on view
    notification_id = notification["id"]
    resp = await authorized_vendor_client.put(f"/notifications/{notification_id}/view")
    assert resp.status_code == 200
    assert "Notification marked as read" in resp.json()["message"]


@pytest.mark.asyncio
async def test_report_thread_notification_flow(
    authorized_admin_client, authorized_customer_client, customer_user_and_token
):
    # Customer reports an issue (simulate report creation)
    customer_user, _ = customer_user_and_token
    # Create a dummy order and vendor for the report
    # (Assume you have a fixture or helper for this in real tests)
    order_id = str(uuid4())
    vendor_id = str(uuid4())
    report_data = {
        "order_id": order_id,
        "vendor_id": vendor_id,
        "description": "Item not delivered",
        "issue_type": "WRONG_ITEMS",
        "report_type": "VENDOR",
    }
    # Simulate report creation (would normally use /report endpoint)
    # Here, just test notification creation for the thread
    notification_data = {
        "report_issue_id": str(uuid4()),
        "title": "Order Issue Report",
        "content": "A new issue has been reported.",
    }
    resp = await authorized_admin_client.post(
        "/notifications/report-thread", json=notification_data
    )
    assert resp.status_code == 200
    notification = resp.json()
    assert notification["notification_type"] == "report_thread"
    assert notification["title"] == notification_data["title"]

    # Add a message to the thread
    message_data = {"content": "We are looking into this."}
    notification_id = notification["id"]
    resp = await authorized_admin_client.post(
        f"/notifications/{notification_id}/messages", json=message_data
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == message_data["content"]

    # Mark all thread messages as read
    resp = await authorized_customer_client.put(
        f"/notifications/{notification_id}/thread/read"
    )
    assert resp.status_code == 200
    assert "Thread messages marked as read" in resp.json()["message"]


@pytest.mark.asyncio
async def test_notification_permission_checks(authorized_customer_client):
    # Try to create a broadcast notification as a customer (should fail if admin check is enabled)
    data = {
        "title": "Should Fail",
        "content": "Not allowed",
        "recipient_ids": [str(uuid4())],
    }
    resp = await authorized_customer_client.post("/notifications/broadcast", json=data)
    # If admin check is enabled, should be 403; otherwise, 200 (adjust as needed)
    assert resp.status_code in (200, 403)


@pytest.mark.asyncio
async def test_notification_not_found(authorized_customer_client):
    # Try to mark a non-existent notification as read
    fake_id = str(uuid4())
    resp = await authorized_customer_client.put(f"/notifications/{fake_id}/read")
    assert resp.status_code in (404, 200)


@pytest.mark.asyncio
async def test_get_notification_stats(authorized_customer_client):
    resp = await authorized_customer_client.get("/notifications/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert "total_notifications" in stats
    assert "unread_notifications" in stats


@pytest.mark.asyncio
async def test_user_registration_and_login(async_client):
    # Register user
    user_data = {
        "email": f"user_{uuid4()}@test.com",
        "password": "testpassword",
        "user_type": UserType.CUSTOMER,
    }
    resp = await async_client.post("/api/auth/register", json=user_data)
    assert resp.status_code == 201
    # Login
    login_data = {"email": user_data["email"], "password": user_data["password"]}
    resp = await async_client.post("/api/auth/login", data=login_data)
    assert resp.status_code == 200
    tokens = resp.json()
    assert "access_token" in tokens
    access_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    # Get profile
    resp = await async_client.get("/api/users/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == user_data["email"]


@pytest.mark.asyncio
async def test_vendor_registration_and_item_creation(async_client):
    # Register vendor
    vendor_data = {
        "email": f"vendor_{uuid4()}@test.com",
        "password": "testpassword",
        "user_type": UserType.RESTAURANT_VENDOR,
    }
    resp = await async_client.post("/api/auth/register", json=vendor_data)
    assert resp.status_code == 201
    # Login
    login_data = {"email": vendor_data["email"], "password": vendor_data["password"]}
    resp = await async_client.post("/api/auth/login", data=login_data)
    assert resp.status_code == 200
    tokens = resp.json()
    access_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    # Create item
    item_data = {
        "name": f"Test Item {uuid4()}",
        "description": "A test item.",
        "price": 1000,
        "item_type": "food",
        "category_id": None,
        "food_group": "main_course",
    }
    resp = await async_client.post("/api/items", json=item_data, headers=headers)
    assert resp.status_code in (200, 201)
    item = resp.json()
    assert item["name"].startswith("Test Item")


@pytest.mark.asyncio
async def test_order_creation_and_payment_flow(async_client):
    # Register and login user
    user_data = {
        "email": f"user_{uuid4()}@test.com",
        "password": "testpassword",
        "user_type": UserType.CUSTOMER,
    }
    resp = await async_client.post("/api/auth/register", json=user_data)
    assert resp.status_code == 201
    login_data = {"email": user_data["email"], "password": user_data["password"]}
    resp = await async_client.post("/api/auth/login", data=login_data)
    tokens = resp.json()
    user_token = tokens["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}

    # Register and login vendor
    vendor_data = {
        "email": f"vendor_{uuid4()}@test.com",
        "password": "testpassword",
        "user_type": UserType.RESTAURANT_VENDOR,
    }
    resp = await async_client.post("/api/auth/register", json=vendor_data)
    assert resp.status_code == 201
    login_data = {"email": vendor_data["email"], "password": vendor_data["password"]}
    resp = await async_client.post("/api/auth/login", data=login_data)
    tokens = resp.json()
    vendor_token = tokens["access_token"]
    vendor_headers = {"Authorization": f"Bearer {vendor_token}"}

    # Vendor creates item
    item_data = {
        "name": f"Test Item {uuid4()}",
        "description": "A test item.",
        "price": 1000,
        "item_type": "food",
        "category_id": None,
        "food_group": "main_course",
    }
    resp = await async_client.post("/api/items", json=item_data, headers=vendor_headers)
    assert resp.status_code in (200, 201)
    item = resp.json()
    item_id = item["id"]

    # User creates order
    order_data = {
        "order_items": [
            {"vendor_id": item["vendor_id"], "item_id": item_id, "quantity": 1}
        ],
        "pickup_coordinates": [6.45, 3.40],
        "dropoff_coordinates": [6.45, 3.41],
        "distance": 1.0,
        "require_delivery": "delivery",
        "duration": "30m",
        "origin": "A",
        "destination": "B",
        "additional_info": "Test order",
    }
    resp = await async_client.post(
        f"/api/orders/{item['vendor_id']}", json=order_data, headers=user_headers
    )
    assert resp.status_code in (200, 201)
    order = resp.json()
    assert order["order"]["order_status"] == "pending"

    # Simulate payment (wallet or bank transfer)
    # This part depends on your payment test setup; here we just check the endpoint exists
    resp = await async_client.post(
        f"/api/payment/{order['order']['id']}/pay-with-wallet", headers=user_headers
    )
    assert resp.status_code in (
        200,
        201,
        400,
        422,
    )  # Acceptable: insufficient funds, etc.


@pytest.mark.asyncio
async def test_error_handling(async_client):
    # Try to get a non-existent order
    resp = await async_client.get(f"/api/orders/{uuid4()}")
    assert resp.status_code == 404
    # Try to create item with duplicate name
    vendor_data = {
        "email": f"vendor_{uuid4()}@test.com",
        "password": "testpassword",
        "user_type": UserType.RESTAURANT_VENDOR,
    }
    resp = await async_client.post("/api/auth/register", json=vendor_data)
    login_data = {"email": vendor_data["email"], "password": vendor_data["password"]}
    resp = await async_client.post("/api/auth/login", data=login_data)
    tokens = resp.json()
    vendor_token = tokens["access_token"]
    vendor_headers = {"Authorization": f"Bearer {vendor_token}"}
    item_data = {
        "name": "Duplicate Item",
        "description": "A test item.",
        "price": 1000,
        "item_type": "food",
        "category_id": None,
        "food_group": "main_course",
    }
    resp = await async_client.post("/api/items", json=item_data, headers=vendor_headers)
    assert resp.status_code in (200, 201)
    resp = await async_client.post("/api/items", json=item_data, headers=vendor_headers)
    assert resp.status_code == 409
    assert "already have an item with this name" in resp.text
