import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timedelta

# from app.services.auth_service import AuthService  # AuthService class doesn't exist
from app.services.order_service import OrderService
from app.services.user_service import UserService
from app.services.item_service import ItemService
from app.models.models import User, Order, Item
from app.schemas.status_schema import UserType, OrderStatus, AccountStatus


# # class TestAuthService:  # Commented out since AuthService doesn't exist
#     """Test authentication service methods."""
#     
#     @pytest.fixture
#     def auth_service(self):
#         return AuthService()
#     
#     async def test_create_user_success(self, auth_service, db_session):
#         """Test successful user creation."""
#         user_data = {
#             "email": "test@example.com",
#             "password": "SecurePass123!",
#             "user_type": UserType.CUSTOMER
#         }
#         
#         with patch('app.services.auth_service.get_password_hash', return_value="hashed_password"):
#             user = await auth_service.create_user(db_session, user_data)
#         
#         assert user.email == user_data["email"]
#         assert user.user_type == user_data["user_type"]
#         assert user.password == "hashed_password"
#     
#     async def test_authenticate_user_success(self, auth_service, db_session, test_user):
#         """Test successful user authentication."""
#         with patch('app.auth.auth.verify_password', return_value=True):
#             authenticated_user = await auth_service.authenticate_user(
#                 db_session, test_user.email, "testpassword123"
#             )
#         
#         assert authenticated_user is not None
#         assert authenticated_user.email == test_user.email
#     
#     async def test_authenticate_user_wrong_password(self, auth_service, db_session, test_user):
#         """Test authentication with wrong password."""
#         with patch('app.auth.auth.verify_password', return_value=False):
#             authenticated_user = await auth_service.authenticate_user(
#                 db_session, test_user.email, "wrongpassword"
#             )
#         
#         assert authenticated_user is None
#     
#     async def test_generate_verification_code(self, auth_service):
#         """Test verification code generation."""
#         code = auth_service.generate_verification_code()
#         
#         assert len(code) == 6
#         assert code.isdigit()
#     
#     async def test_verify_email_success(self, auth_service, db_session):
#         """Test successful email verification."""
#         user = User(
#             email="test@example.com",
#             password="hashed",
#             is_email_verified=False,
#             email_verification_code="123456",
#             email_verification_expires=datetime.utcnow() + timedelta(hours=1)
#         )
#         db_session.add(user)
#         await db_session.commit()
#         
#         result = await auth_service.verify_email(db_session, user.email, "123456")
#         
#         assert result is True
#         assert user.is_email_verified is True
#     
#     async def test_verify_email_expired_code(self, auth_service, db_session):
#         """Test email verification with expired code."""
#         user = User(
#             email="test@example.com",
#             password="hashed",
#             is_email_verified=False,
#             email_verification_code="123456",
#             email_verification_expires=datetime.utcnow() - timedelta(hours=1)  # Expired
#         )
#         db_session.add(user)
#         await db_session.commit()
#         
#         result = await auth_service.verify_email(db_session, user.email, "123456")
#         
#         assert result is False
# 

class TestOrderService:
    """Test order service methods."""
    
    @pytest.fixture
    def order_service(self):
        return OrderService()
    
    async def test_create_order_success(self, order_service, db_session, test_user, test_vendor):
        """Test successful order creation."""
        # Create test item
        item = Item(
            id=uuid4(),
            name="Test Item",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True
        )
        db_session.add(item)
        await db_session.commit()
        
        order_data = {
            "items": [{"item_id": item.id, "quantity": 2}],
            "delivery_address": "Test Address"
        }
        
        with patch('app.services.order_service.calculate_delivery_fee', return_value=Decimal("5.00")):
            order = await order_service.create_order(db_session, test_user.id, order_data)
        
        assert order.customer_id == test_user.id
        assert order.status == OrderStatus.PENDING
        assert order.total_amount > 0
    
    async def test_calculate_order_total(self, order_service):
        """Test order total calculation."""
        items = [
            {"price": Decimal("10.00"), "quantity": 2},
            {"price": Decimal("5.50"), "quantity": 1}
        ]
        delivery_fee = Decimal("3.00")
        
        total = order_service.calculate_order_total(items, delivery_fee)
        
        # Expected: (10.00 * 2) + (5.50 * 1) + 3.00 = 28.50
        assert total == Decimal("28.50")
    
    async def test_update_order_status_success(self, order_service, db_session, test_user):
        """Test successful order status update."""
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("25.99")
        )
        db_session.add(order)
        await db_session.commit()
        
        updated_order = await order_service.update_order_status(
            db_session, order.id, OrderStatus.CONFIRMED
        )
        
        assert updated_order.status == OrderStatus.CONFIRMED
    
    async def test_cancel_order_success(self, order_service, db_session, test_user):
        """Test successful order cancellation."""
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("25.99")
        )
        db_session.add(order)
        await db_session.commit()
        
        result = await order_service.cancel_order(db_session, order.id, test_user.id)
        
        assert result is True
        assert order.status == OrderStatus.CANCELLED
    
    async def test_cancel_order_not_allowed(self, order_service, db_session, test_user):
        """Test cancelling order that cannot be cancelled."""
        order = Order(
            id=uuid4(),
            customer_id=test_user.id,
            status=OrderStatus.PREPARING,  # Cannot cancel
            total_amount=Decimal("25.99")
        )
        db_session.add(order)
        await db_session.commit()
        
        result = await order_service.cancel_order(db_session, order.id, test_user.id)
        
        assert result is False
    
    async def test_get_user_orders(self, order_service, db_session, test_user):
        """Test getting user's orders."""
        # Create test orders
        orders = [
            Order(
                id=uuid4(),
                customer_id=test_user.id,
                status=OrderStatus.PENDING,
                total_amount=Decimal("25.99")
            ),
            Order(
                id=uuid4(),
                customer_id=test_user.id,
                status=OrderStatus.COMPLETED,
                total_amount=Decimal("30.99")
            )
        ]
        db_session.add_all(orders)
        await db_session.commit()
        
        user_orders = await order_service.get_user_orders(db_session, test_user.id)
        
        assert len(user_orders) >= 2
        assert all(order.customer_id == test_user.id for order in user_orders)


class TestUserService:
    """Test user service methods."""
    
    @pytest.fixture
    def user_service(self):
        return UserService()
    
    async def test_get_user_by_email(self, user_service, db_session, test_user):
        """Test getting user by email."""
        user = await user_service.get_user_by_email(db_session, test_user.email)
        
        assert user is not None
        assert user.email == test_user.email
    
    async def test_get_user_by_email_not_found(self, user_service, db_session):
        """Test getting non-existent user by email."""
        user = await user_service.get_user_by_email(db_session, "nonexistent@example.com")
        
        assert user is None
    
    async def test_update_user_profile(self, user_service, db_session, test_user):
        """Test updating user profile."""
        update_data = {
            "first_name": "John",
            "last_name": "Doe"
        }
        
        updated_user = await user_service.update_user_profile(
            db_session, test_user.id, update_data
        )
        
        assert updated_user.profile.first_name == "John"
        assert updated_user.profile.last_name == "Doe"
    
    async def test_deactivate_user(self, user_service, db_session, test_user):
        """Test user deactivation."""
        result = await user_service.deactivate_user(db_session, test_user.id)
        
        assert result is True
        assert test_user.account_status == AccountStatus.INACTIVE
    
    async def test_change_password_success(self, user_service, db_session, test_user):
        """Test successful password change."""
        with patch('app.auth.auth.verify_password', return_value=True), \
             patch('app.auth.auth.get_password_hash', return_value="new_hashed_password"):
            
            result = await user_service.change_password(
                db_session, test_user.id, "oldpassword", "newpassword"
            )
        
        assert result is True
        assert test_user.password == "new_hashed_password"
    
    async def test_change_password_wrong_current(self, user_service, db_session, test_user):
        """Test password change with wrong current password."""
        with patch('app.auth.auth.verify_password', return_value=False):
            result = await user_service.change_password(
                db_session, test_user.id, "wrongpassword", "newpassword"
            )
        
        assert result is False
    
    async def test_update_user_location(self, user_service, db_session, test_user):
        """Test updating user location."""
        location_data = {
            "latitude": 40.7128,
            "longitude": -74.0060,
            "address": "New York, NY"
        }
        
        updated_user = await user_service.update_user_location(
            db_session, test_user.id, location_data
        )
        
        coords = updated_user.current_user_location_coords
        assert coords["latitude"] == 40.7128
        assert coords["longitude"] == -74.0060
    
    async def test_get_nearby_users(self, user_service, db_session, test_user):
        """Test getting nearby users."""
        # Create users with locations
        users_with_locations = []
        for i in range(3):
            user = User(
                email=f"user{i}@example.com",
                password="hashed",
                user_type=UserType.RIDER,
                current_user_location_coords={
                    "latitude": 40.7128 + (i * 0.01),
                    "longitude": -74.0060 + (i * 0.01)
                }
            )
            users_with_locations.append(user)
        
        db_session.add_all(users_with_locations)
        await db_session.commit()
        
        nearby_users = await user_service.get_nearby_users(
            db_session, 40.7128, -74.0060, 5000, UserType.RIDER
        )
        
        assert len(nearby_users) >= 1


class TestItemService:
    """Test item service methods."""
    
    @pytest.fixture
    def item_service(self):
        return ItemService()
    
    async def test_create_item_success(self, item_service, db_session, test_vendor):
        """Test successful item creation."""
        item_data = {
            "name": "Test Pizza",
            "description": "Delicious pizza",
            "price": Decimal("15.99"),
            "category": "food",
            "is_available": True
        }
        
        item = await item_service.create_item(db_session, test_vendor.id, item_data)
        
        assert item.name == item_data["name"]
        assert item.price == item_data["price"]
        assert item.vendor_id == test_vendor.id
    
    async def test_update_item_success(self, item_service, db_session, test_vendor):
        """Test successful item update."""
        item = Item(
            id=uuid4(),
            name="Original Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True
        )
        db_session.add(item)
        await db_session.commit()
        
        update_data = {
            "name": "Updated Pizza",
            "price": Decimal("18.99")
        }
        
        updated_item = await item_service.update_item(
            db_session, item.id, test_vendor.id, update_data
        )
        
        assert updated_item.name == "Updated Pizza"
        assert updated_item.price == Decimal("18.99")
    
    async def test_update_item_not_owner(self, item_service, db_session, test_vendor, test_user):
        """Test updating item by non-owner."""
        item = Item(
            id=uuid4(),
            name="Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True
        )
        db_session.add(item)
        await db_session.commit()
        
        update_data = {"name": "Hacked Pizza"}
        
        result = await item_service.update_item(
            db_session, item.id, test_user.id, update_data  # Wrong user
        )
        
        assert result is None
    
    async def test_delete_item_success(self, item_service, db_session, test_vendor):
        """Test successful item deletion."""
        item = Item(
            id=uuid4(),
            name="Pizza to Delete",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True
        )
        db_session.add(item)
        await db_session.commit()
        
        result = await item_service.delete_item(db_session, item.id, test_vendor.id)
        
        assert result is True
    
    async def test_search_items(self, item_service, db_session, test_vendor):
        """Test item search functionality."""
        # Create test items
        items = [
            Item(
                id=uuid4(),
                name="Margherita Pizza",
                price=Decimal("15.99"),
                vendor_id=test_vendor.id,
                is_available=True
            ),
            Item(
                id=uuid4(),
                name="Pepperoni Pizza",
                price=Decimal("18.99"),
                vendor_id=test_vendor.id,
                is_available=True
            ),
            Item(
                id=uuid4(),
                name="Chicken Burger",
                price=Decimal("12.99"),
                vendor_id=test_vendor.id,
                is_available=True
            )
        ]
        db_session.add_all(items)
        await db_session.commit()
        
        # Search for pizza
        pizza_items = await item_service.search_items(db_session, "pizza")
        
        assert len(pizza_items) >= 2
        assert all("pizza" in item.name.lower() for item in pizza_items)
    
    async def test_get_vendor_items(self, item_service, db_session, test_vendor):
        """Test getting items for a specific vendor."""
        # Create items for vendor
        items = [
            Item(
                id=uuid4(),
                name="Item 1",
                price=Decimal("10.99"),
                vendor_id=test_vendor.id,
                is_available=True
            ),
            Item(
                id=uuid4(),
                name="Item 2",
                price=Decimal("15.99"),
                vendor_id=test_vendor.id,
                is_available=False
            )
        ]
        db_session.add_all(items)
        await db_session.commit()
        
        vendor_items = await item_service.get_vendor_items(db_session, test_vendor.id)
        
        assert len(vendor_items) >= 2
        assert all(item.vendor_id == test_vendor.id for item in vendor_items)
    
    async def test_toggle_item_availability(self, item_service, db_session, test_vendor):
        """Test toggling item availability."""
        item = Item(
            id=uuid4(),
            name="Toggle Item",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True
        )
        db_session.add(item)
        await db_session.commit()
        
        # Toggle to unavailable
        toggled_item = await item_service.toggle_item_availability(
            db_session, item.id, test_vendor.id
        )
        
        assert toggled_item.is_available is False
        
        # Toggle back to available
        toggled_item = await item_service.toggle_item_availability(
            db_session, item.id, test_vendor.id
        )
        
        assert toggled_item.is_available is True
