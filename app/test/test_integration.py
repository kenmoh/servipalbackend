import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
from uuid import uuid4
from decimal import Decimal

from app.models.models import User, Item, Order
from app.schemas.status_schema import OrderStatus, PaymentStatus, UserType


class TestFullOrderFlow:
    """Test complete order flow from creation to completion."""
    
    async def test_complete_order_flow(self, client: AsyncClient, db_session):
        """Test complete order flow: register -> login -> create item -> order -> pay -> complete."""
        
        # 1. Register customer
        customer_data = {
            "email": "customer@test.com",
            "password": "SecurePass123!",
            "user_type": "customer"
        }
        
        with patch('app.services.auth_service.send_verification_email', new_callable=AsyncMock):
            response = await client.post("/auth/register", json=customer_data)
        assert response.status_code == 201
        
        # 2. Register vendor
        vendor_data = {
            "email": "vendor@test.com",
            "password": "SecurePass123!",
            "user_type": "restaurant_vendor"
        }
        
        with patch('app.services.auth_service.send_verification_email', new_callable=AsyncMock):
            response = await client.post("/auth/register", json=vendor_data)
        assert response.status_code == 201
        
        # 3. Login customer
        customer_login = {
            "username": customer_data["email"],
            "password": customer_data["password"]
        }
        response = await client.post("/auth/login", data=customer_login)
        assert response.status_code == 200
        customer_token = response.json()["access_token"]
        customer_headers = {"Authorization": f"Bearer {customer_token}"}
        
        # 4. Login vendor
        vendor_login = {
            "username": vendor_data["email"],
            "password": vendor_data["password"]
        }
        response = await client.post("/auth/login", data=vendor_login)
        assert response.status_code == 200
        vendor_token = response.json()["access_token"]
        vendor_headers = {"Authorization": f"Bearer {vendor_token}"}
        
        # 5. Vendor creates item
        item_data = {
            "name": "Delicious Pizza",
            "description": "Best pizza in town",
            "price": 19.99,
            "category": "food",
            "is_available": True
        }
        
        with patch('app.utils.s3_service.upload_file', return_value="https://s3.amazonaws.com/image.jpg"):
            response = await client.post("/items", json=item_data, headers=vendor_headers)
        assert response.status_code == 201
        item_id = response.json()["id"]
        
        # 6. Customer creates order
        order_data = {
            "items": [
                {
                    "item_id": item_id,
                    "quantity": 2,
                    "special_instructions": "Extra cheese please"
                }
            ],
            "delivery_address": "123 Test Street, Test City",
            "order_type": "delivery"
        }
        
        with patch('app.services.order_service.calculate_delivery_fee', return_value=5.00):
            response = await client.post("/orders", json=order_data, headers=customer_headers)
        assert response.status_code == 201
        order_id = response.json()["id"]
        
        # 7. Vendor accepts order
        response = await client.post(f"/orders/{order_id}/accept", headers=vendor_headers)
        assert response.status_code == 200
        assert response.json()["status"] == OrderStatus.CONFIRMED
        
        # 8. Customer pays for order
        payment_data = {
            "payment_method": "card"
        }
        
        with patch('app.services.payment_service.process_payment', return_value={"status": "success"}):
            response = await client.post(f"/orders/{order_id}/pay", json=payment_data, headers=customer_headers)
        assert response.status_code == 200
        
        # 9. Vendor marks order as preparing
        response = await client.post(f"/orders/{order_id}/preparing", headers=vendor_headers)
        assert response.status_code == 200
        assert response.json()["status"] == OrderStatus.PREPARING
        
        # 10. Vendor marks order as ready
        response = await client.post(f"/orders/{order_id}/ready", headers=vendor_headers)
        assert response.status_code == 200
        assert response.json()["status"] == OrderStatus.READY
        
        # 11. Order gets delivered (by rider)
        response = await client.post(f"/orders/{order_id}/delivered", headers=vendor_headers)
        assert response.status_code == 200
        assert response.json()["status"] == OrderStatus.DELIVERED


class TestUserRoleInteractions:
    """Test interactions between different user roles."""
    
    async def test_vendor_customer_interaction(self, client: AsyncClient, vendor_auth_headers, auth_headers, db_session, test_vendor):
        """Test vendor-customer interaction through orders."""
        
        # Vendor creates item
        item = Item(
            id=uuid4(),
            name="Test Item",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True
        )
        db_session.add(item)
        await db_session.commit()
        
        # Customer creates order
        order_data = {
            "items": [{"item_id": str(item.id), "quantity": 1}],
            "delivery_address": "Test Address",
            "order_type": "delivery"
        }
        
        with patch('app.services.order_service.calculate_delivery_fee', return_value=5.00):
            response = await client.post("/orders", json=order_data, headers=auth_headers)
        assert response.status_code == 201
        order_id = response.json()["id"]
        
        # Vendor can see the order
        response = await client.get(f"/orders/vendor/{order_id}", headers=vendor_auth_headers)
        assert response.status_code == 200
        
        # Customer cannot access vendor-specific endpoints
        response = await client.get(f"/orders/vendor/{order_id}", headers=auth_headers)
        assert response.status_code == 403
    
    async def test_rider_order_assignment(self, client: AsyncClient, rider_auth_headers, db_session, test_rider, test_user):
        """Test rider assignment to orders."""
        
        # Create order
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.READY,
            total_amount=Decimal("25.99")
        )
        db_session.add(order)
        await db_session.commit()
        
        # Rider accepts delivery
        response = await client.post(f"/orders/{order.id}/accept-delivery", headers=rider_auth_headers)
        assert response.status_code == 200
        
        # Rider can see assigned orders
        response = await client.get("/orders/my-deliveries", headers=rider_auth_headers)
        assert response.status_code == 200
        orders = response.json()
        assert any(o["id"] == str(order.id) for o in orders)


class TestErrorHandling:
    """Test error handling across the application."""
    
    async def test_database_error_handling(self, client: AsyncClient, auth_headers):
        """Test handling of database errors."""
        
        # Simulate database connection error
        with patch('app.database.database.get_db', side_effect=Exception("Database connection failed")):
            response = await client.get("/users/profile", headers=auth_headers)
        
        assert response.status_code == 500
        assert "internal server error" in response.json()["detail"].lower()
    
    async def test_external_service_error_handling(self, client: AsyncClient, vendor_auth_headers):
        """Test handling of external service errors."""
        
        item_data = {
            "name": "Test Item",
            "price": 15.99,
            "category": "food"
        }
        
        # Simulate S3 upload failure
        with patch('app.utils.s3_service.upload_file', side_effect=Exception("S3 upload failed")):
            response = await client.post("/items", json=item_data, headers=vendor_auth_headers)
        
        # Should handle gracefully
        assert response.status_code in [500, 503]
    
    async def test_payment_service_error_handling(self, client: AsyncClient, auth_headers, db_session, test_user):
        """Test handling of payment service errors."""
        
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.CONFIRMED,
            total_amount=Decimal("25.99"),
            payment_status=PaymentStatus.PENDING
        )
        db_session.add(order)
        await db_session.commit()
        
        payment_data = {"payment_method": "card"}
        
        # Simulate payment service failure
        with patch('app.services.payment_service.process_payment', side_effect=Exception("Payment failed")):
            response = await client.post(f"/orders/{order.id}/pay", json=payment_data, headers=auth_headers)
        
        assert response.status_code in [400, 500]
        assert "payment" in response.json()["detail"].lower()


class TestConcurrency:
    """Test concurrent operations."""
    
    async def test_concurrent_order_creation(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test handling of concurrent order creation."""
        
        # Create item with limited stock
        item = Item(
            id=uuid4(),
            name="Limited Item",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            stock_quantity=1  # Only 1 available
        )
        db_session.add(item)
        await db_session.commit()
        
        order_data = {
            "items": [{"item_id": str(item.id), "quantity": 1}],
            "delivery_address": "Test Address",
            "order_type": "delivery"
        }
        
        # Simulate concurrent requests
        import asyncio
        
        async def create_order():
            with patch('app.services.order_service.calculate_delivery_fee', return_value=5.00):
                return await client.post("/orders", json=order_data, headers=auth_headers)
        
        # Create multiple concurrent orders
        tasks = [create_order() for _ in range(3)]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Only one should succeed, others should fail due to stock
        success_count = sum(1 for r in responses if hasattr(r, 'status_code') and r.status_code == 201)
        assert success_count <= 1


class TestDataConsistency:
    """Test data consistency across operations."""
    
    async def test_order_total_calculation_consistency(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test that order totals are calculated consistently."""
        
        # Create items with known prices
        items = [
            Item(
                id=uuid4(),
                name="Item 1",
                price=Decimal("10.00"),
                vendor_id=test_vendor.id,
                is_available=True
            ),
            Item(
                id=uuid4(),
                name="Item 2",
                price=Decimal("15.50"),
                vendor_id=test_vendor.id,
                is_available=True
            )
        ]
        db_session.add_all(items)
        await db_session.commit()
        
        order_data = {
            "items": [
                {"item_id": str(items[0].id), "quantity": 2},  # 2 * 10.00 = 20.00
                {"item_id": str(items[1].id), "quantity": 1}   # 1 * 15.50 = 15.50
            ],
            "delivery_address": "Test Address",
            "order_type": "delivery"
        }
        
        with patch('app.services.order_service.calculate_delivery_fee', return_value=5.00):
            response = await client.post("/orders", json=order_data, headers=auth_headers)
        
        assert response.status_code == 201
        data = response.json()
        
        # Expected: 20.00 + 15.50 + 5.00 (delivery) = 40.50
        expected_total = Decimal("40.50")
        assert Decimal(str(data["total_amount"])) == expected_total
    
    async def test_inventory_consistency(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test inventory consistency after order operations."""
        
        initial_stock = 10
        item = Item(
            id=uuid4(),
            name="Stock Item",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            stock_quantity=initial_stock
        )
        db_session.add(item)
        await db_session.commit()
        
        order_data = {
            "items": [{"item_id": str(item.id), "quantity": 3}],
            "delivery_address": "Test Address",
            "order_type": "delivery"
        }
        
        with patch('app.services.order_service.calculate_delivery_fee', return_value=5.00):
            response = await client.post("/orders", json=order_data, headers=auth_headers)
        
        assert response.status_code == 201
        
        # Check that stock was reduced
        response = await client.get(f"/items/{item.id}")
        assert response.status_code == 200
        updated_item = response.json()
        
        # Stock should be reduced by ordered quantity
        expected_stock = initial_stock - 3
        assert updated_item["stock_quantity"] == expected_stock
