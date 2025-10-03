import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import uuid4
from decimal import Decimal

from app.models.models import User, Order, Item, OrderItem
from app.schemas.status_schema import OrderStatus, OrderType, PaymentStatus, UserType


class TestOrderCreation:
    """Test order creation endpoints."""
    
    async def test_create_order_success(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test successful order creation."""
        # Create test item
        item = Item(
            id=uuid4(),
            name="Test Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True
        )
        db_session.add(item)
        await db_session.commit()
        
        order_data = {
            "items": [
                {
                    "item_id": str(item.id),
                    "quantity": 2,
                    "special_instructions": "Extra cheese"
                }
            ],
            "delivery_address": "123 Main St, City",
            "order_type": "delivery"
        }
        
        with patch('app.services.order_service.calculate_delivery_fee', return_value=5.00):
            response = await client.post("/orders", json=order_data, headers=auth_headers)
        
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == OrderStatus.PENDING
        assert len(data["items"]) == 1
        assert data["total_amount"] > 0
    
    async def test_create_order_invalid_item(self, client: AsyncClient, auth_headers):
        """Test order creation with invalid item."""
        order_data = {
            "items": [
                {
                    "item_id": str(uuid4()),  # Non-existent item
                    "quantity": 1
                }
            ],
            "delivery_address": "123 Main St, City",
            "order_type": "delivery"
        }
        
        response = await client.post("/orders", json=order_data, headers=auth_headers)
        assert response.status_code == 404
        assert "item not found" in response.json()["detail"].lower()
    
    async def test_create_order_unavailable_item(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test order creation with unavailable item."""
        # Create unavailable item
        item = Item(
            id=uuid4(),
            name="Unavailable Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=False
        )
        db_session.add(item)
        await db_session.commit()
        
        order_data = {
            "items": [
                {
                    "item_id": str(item.id),
                    "quantity": 1
                }
            ],
            "delivery_address": "123 Main St, City",
            "order_type": "delivery"
        }
        
        response = await client.post("/orders", json=order_data, headers=auth_headers)
        assert response.status_code == 400
        assert "not available" in response.json()["detail"].lower()
    
    async def test_create_order_zero_quantity(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test order creation with zero quantity."""
        item = Item(
            id=uuid4(),
            name="Test Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True
        )
        db_session.add(item)
        await db_session.commit()
        
        order_data = {
            "items": [
                {
                    "item_id": str(item.id),
                    "quantity": 0
                }
            ],
            "delivery_address": "123 Main St, City",
            "order_type": "delivery"
        }
        
        response = await client.post("/orders", json=order_data, headers=auth_headers)
        assert response.status_code == 422


class TestOrderRetrieval:
    """Test order retrieval endpoints."""
    
    async def test_get_user_orders(self, client: AsyncClient, auth_headers, db_session, test_user):
        """Test getting user's orders."""
        # Create test order
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY
        )
        db_session.add(order)
        await db_session.commit()
        
        response = await client.get("/orders/my-orders", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["id"] == str(order.id)
    
    async def test_get_order_by_id_success(self, client: AsyncClient, auth_headers, db_session, test_user):
        """Test getting specific order by ID."""
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY
        )
        db_session.add(order)
        await db_session.commit()
        
        response = await client.get(f"/orders/{order.id}", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(order.id)
        assert data["status"] == OrderStatus.PENDING
    
    async def test_get_order_by_id_not_found(self, client: AsyncClient, auth_headers):
        """Test getting non-existent order."""
        fake_id = uuid4()
        response = await client.get(f"/orders/{fake_id}", headers=auth_headers)
        
        assert response.status_code == 404
        assert "order not found" in response.json()["detail"].lower()
    
    async def test_get_order_unauthorized_access(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test accessing another user's order."""
        # Create order for different user
        order = Order(
            id=uuid4(),
            customer_id=test_vendor.id,  # Different user
            status=OrderStatus.PENDING,
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY
        )
        db_session.add(order)
        await db_session.commit()
        
        response = await client.get(f"/orders/{order.id}", headers=auth_headers)
        
        assert response.status_code == 403
        assert "access" in response.json()["detail"].lower()


class TestOrderStatusUpdates:
    """Test order status update endpoints."""
    
    async def test_cancel_order_success(self, client: AsyncClient, auth_headers, db_session, test_user):
        """Test successful order cancellation."""
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY
        )
        db_session.add(order)
        await db_session.commit()
        
        response = await client.post(f"/orders/{order.id}/cancel", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CANCELLED
    
    async def test_cancel_order_already_processing(self, client: AsyncClient, auth_headers, db_session, test_user):
        """Test cancelling order that's already being processed."""
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.PREPARING,  # Cannot cancel
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY
        )
        db_session.add(order)
        await db_session.commit()
        
        response = await client.post(f"/orders/{order.id}/cancel", headers=auth_headers)
        
        assert response.status_code == 400
        assert "cannot be cancelled" in response.json()["detail"].lower()
    
    async def test_vendor_accept_order(self, client: AsyncClient, vendor_auth_headers, db_session, test_vendor):
        """Test vendor accepting an order."""
        order = Order(
            id=uuid4(),
            vendor_id=test_vendor.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY
        )
        db_session.add(order)
        await db_session.commit()
        
        response = await client.post(f"/orders/{order.id}/accept", headers=vendor_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CONFIRMED
    
    async def test_vendor_reject_order(self, client: AsyncClient, vendor_auth_headers, db_session, test_vendor):
        """Test vendor rejecting an order."""
        order = Order(
            id=uuid4(),
            vendor_id=test_vendor.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY
        )
        db_session.add(order)
        await db_session.commit()
        
        rejection_data = {
            "reason": "Out of ingredients"
        }
        
        response = await client.post(
            f"/orders/{order.id}/reject", 
            json=rejection_data,
            headers=vendor_auth_headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == OrderStatus.CANCELLED


class TestOrderPayment:
    """Test order payment endpoints."""
    
    async def test_initiate_payment_success(self, client: AsyncClient, auth_headers, db_session, test_user):
        """Test initiating payment for an order."""
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.CONFIRMED,
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY,
            payment_status=PaymentStatus.PENDING
        )
        db_session.add(order)
        await db_session.commit()
        
        payment_data = {
            "payment_method": "card",
            "card_details": {
                "number": "4111111111111111",
                "expiry": "12/25",
                "cvv": "123"
            }
        }
        
        with patch('app.services.payment_service.process_payment', return_value={"status": "success"}):
            response = await client.post(
                f"/orders/{order.id}/pay", 
                json=payment_data,
                headers=auth_headers
            )
        
        assert response.status_code == 200
        data = response.json()
        assert "payment_url" in data or "payment_status" in data
    
    async def test_payment_already_paid_order(self, client: AsyncClient, auth_headers, db_session, test_user):
        """Test payment for already paid order."""
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.CONFIRMED,
            total_amount=Decimal("25.99"),
            order_type=OrderType.DELIVERY,
            payment_status=PaymentStatus.COMPLETED
        )
        db_session.add(order)
        await db_session.commit()
        
        payment_data = {
            "payment_method": "card"
        }
        
        response = await client.post(
            f"/orders/{order.id}/pay", 
            json=payment_data,
            headers=auth_headers
        )
        
        assert response.status_code == 400
        assert "already paid" in response.json()["detail"].lower()


class TestOrderFiltering:
    """Test order filtering and search."""
    
    async def test_filter_orders_by_status(self, client: AsyncClient, auth_headers, db_session, test_user):
        """Test filtering orders by status."""
        # Create orders with different statuses
        orders = [
            Order(
                id=uuid4(),
                customer_id=test_user.id,
                status=OrderStatus.PENDING,
                total_amount=Decimal("25.99"),
                order_type=OrderType.DELIVERY
            ),
            Order(
                id=uuid4(),
                customer_id=test_user.id,
                status=OrderStatus.COMPLETED,
                total_amount=Decimal("30.99"),
                order_type=OrderType.DELIVERY
            )
        ]
        db_session.add_all(orders)
        await db_session.commit()
        
        params = {"status": "pending"}
        response = await client.get("/orders/my-orders", params=params, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert all(order["status"] == OrderStatus.PENDING for order in data)
    
    async def test_filter_orders_by_date_range(self, client: AsyncClient, auth_headers):
        """Test filtering orders by date range."""
        params = {
            "start_date": "2024-01-01",
            "end_date": "2024-12-31"
        }
        
        response = await client.get("/orders/my-orders", params=params, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    async def test_pagination_orders(self, client: AsyncClient, auth_headers):
        """Test order pagination."""
        params = {
            "page": 1,
            "limit": 10
        }
        
        response = await client.get("/orders/my-orders", params=params, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) <= 10
