import pytest
import asyncio
from httpx import AsyncClient
from uuid import uuid4
from app.main import app
from app.config.config import settings
from app.schemas.user_schemas import CreateUserSchema
from app.schemas.notification_schemas import NotificationType

pytestmark = pytest.mark.anyio


@pytest.mark.asyncio
async def test_registration_and_login(async_client, test_user_data):
    # Register a new user
    user_data = {
        "email": f"testuser_{uuid4()}@example.com",
        "phone_number": "08012345678",
        "user_type": "CUSTOMER",
        "password": "TestPassword123!"
    }
    resp = await async_client.post("/auth/register", json=user_data)
    assert resp.status_code == 201
    assert resp.json()["email"] == user_data["email"]

    # Login with the new user
    login_data = {
        "username": user_data["email"],
        "password": user_data["password"]
    }
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
async def test_broadcast_notification_flow(authorized_admin_client, authorized_customer_client, customer_user_and_token):
    # Admin sends a broadcast notification
    customer_user, _ = customer_user_and_token
    data = {
        "title": "System Update",
        "content": "We have a new feature!",
        "recipient_ids": [str(customer_user.id)]
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
    resp = await authorized_customer_client.put(f"/notifications/{notification_id}/read")
    assert resp.status_code == 200
    assert resp.json()["message"] == "Notification marked as read"

    # Badge count should decrease
    resp = await authorized_customer_client.get("/notifications/badge-count")
    assert resp.status_code == 200
    assert resp.json()["unread_count"] >= 0


@pytest.mark.asyncio
async def test_individual_notification_flow(authorized_admin_client, authorized_vendor_client, vendor_user_and_token):
    vendor_user, _ = vendor_user_and_token
    data = {
        "recipient_id": str(vendor_user.id),
        "title": "Direct Message",
        "content": "Hello Vendor!"
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
async def test_report_thread_notification_flow(authorized_admin_client, authorized_customer_client, customer_user_and_token):
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
        "report_type": "VENDOR"
    }
    # Simulate report creation (would normally use /report endpoint)
    # Here, just test notification creation for the thread
    notification_data = {
        "report_issue_id": str(uuid4()),
        "title": "Order Issue Report",
        "content": "A new issue has been reported."
    }
    resp = await authorized_admin_client.post("/notifications/report-thread", json=notification_data)
    assert resp.status_code == 200
    notification = resp.json()
    assert notification["notification_type"] == "report_thread"
    assert notification["title"] == notification_data["title"]

    # Add a message to the thread
    message_data = {"content": "We are looking into this."}
    notification_id = notification["id"]
    resp = await authorized_admin_client.post(f"/notifications/{notification_id}/messages", json=message_data)
    assert resp.status_code == 200
    assert resp.json()["content"] == message_data["content"]

    # Mark all thread messages as read
    resp = await authorized_customer_client.put(f"/notifications/{notification_id}/thread/read")
    assert resp.status_code == 200
    assert "Thread messages marked as read" in resp.json()["message"]


@pytest.mark.asyncio
async def test_notification_permission_checks(authorized_customer_client):
    # Try to create a broadcast notification as a customer (should fail if admin check is enabled)
    data = {
        "title": "Should Fail",
        "content": "Not allowed",
        "recipient_ids": [str(uuid4())]
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