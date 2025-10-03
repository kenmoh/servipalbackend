import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import uuid4

from app.models.models import User, Profile
from app.schemas.status_schema import UserType, AccountStatus


class TestUserProfile:
    """Test user profile endpoints."""
    
    async def test_get_user_profile_success(self, client: AsyncClient, auth_headers, test_user):
        """Test getting user profile successfully."""
        response = await client.get("/users/profile", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email
        assert data["user_type"] == test_user.user_type
        assert "password" not in data
    
    async def test_get_user_profile_unauthorized(self, client: AsyncClient):
        """Test getting profile without authentication."""
        response = await client.get("/users/profile")
        assert response.status_code == 401
    
    async def test_update_user_profile_success(self, client: AsyncClient, auth_headers, db_session):
        """Test updating user profile successfully."""
        update_data = {
            "first_name": "John",
            "last_name": "Doe",
            "phone_number": "+1234567890"
        }
        
        response = await client.put("/users/profile", json=update_data, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["first_name"] == update_data["first_name"]
        assert data["last_name"] == update_data["last_name"]
    
    async def test_update_user_profile_invalid_phone(self, client: AsyncClient, auth_headers):
        """Test updating profile with invalid phone number."""
        update_data = {
            "phone_number": "invalid_phone"
        }
        
        response = await client.put("/users/profile", json=update_data, headers=auth_headers)
        assert response.status_code == 422


class TestUserLocation:
    """Test user location endpoints."""
    
    async def test_update_user_location_success(self, client: AsyncClient, auth_headers):
        """Test updating user location successfully."""
        location_data = {
            "latitude": 40.7128,
            "longitude": -74.0060,
            "address": "New York, NY"
        }
        
        response = await client.put("/users/location", json=location_data, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["latitude"] == location_data["latitude"]
        assert data["longitude"] == location_data["longitude"]
    
    async def test_update_user_location_invalid_coordinates(self, client: AsyncClient, auth_headers):
        """Test updating location with invalid coordinates."""
        location_data = {
            "latitude": 200,  # Invalid latitude
            "longitude": -74.0060
        }
        
        response = await client.put("/users/location", json=location_data, headers=auth_headers)
        assert response.status_code == 422
    
    async def test_get_nearby_users(self, client: AsyncClient, auth_headers):
        """Test getting nearby users."""
        params = {
            "latitude": 40.7128,
            "longitude": -74.0060,
            "radius": 5000  # 5km
        }
        
        response = await client.get("/users/nearby", params=params, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestUserManagement:
    """Test user management endpoints."""
    
    async def test_deactivate_user_account(self, client: AsyncClient, auth_headers):
        """Test deactivating user account."""
        response = await client.post("/users/deactivate", headers=auth_headers)
        
        assert response.status_code == 200
        assert "deactivated" in response.json()["message"].lower()
    
    async def test_change_password_success(self, client: AsyncClient, auth_headers):
        """Test changing password successfully."""
        password_data = {
            "current_password": "testpassword123",
            "new_password": "NewSecurePass123!",
            "confirm_password": "NewSecurePass123!"
        }
        
        response = await client.post("/users/change-password", json=password_data, headers=auth_headers)
        
        assert response.status_code == 200
        assert "password changed" in response.json()["message"].lower()
    
    async def test_change_password_wrong_current(self, client: AsyncClient, auth_headers):
        """Test changing password with wrong current password."""
        password_data = {
            "current_password": "wrongpassword",
            "new_password": "NewSecurePass123!",
            "confirm_password": "NewSecurePass123!"
        }
        
        response = await client.post("/users/change-password", json=password_data, headers=auth_headers)
        
        assert response.status_code == 400
        assert "current password" in response.json()["detail"].lower()
    
    async def test_change_password_mismatch(self, client: AsyncClient, auth_headers):
        """Test changing password with mismatched confirmation."""
        password_data = {
            "current_password": "testpassword123",
            "new_password": "NewSecurePass123!",
            "confirm_password": "DifferentPassword123!"
        }
        
        response = await client.post("/users/change-password", json=password_data, headers=auth_headers)
        
        assert response.status_code == 400
        assert "passwords do not match" in response.json()["detail"].lower()


class TestUserRoles:
    """Test user role-specific functionality."""
    
    async def test_vendor_profile_access(self, client: AsyncClient, vendor_auth_headers):
        """Test vendor-specific profile access."""
        response = await client.get("/users/vendor/profile", headers=vendor_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["user_type"] == UserType.RESTAURANT_VENDOR
    
    async def test_customer_cannot_access_vendor_routes(self, client: AsyncClient, auth_headers):
        """Test that customers cannot access vendor-only routes."""
        response = await client.get("/users/vendor/profile", headers=auth_headers)
        
        assert response.status_code == 403
        assert "permission" in response.json()["detail"].lower()
    
    async def test_rider_profile_access(self, client: AsyncClient, rider_auth_headers):
        """Test rider-specific profile access."""
        response = await client.get("/users/rider/profile", headers=rider_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["user_type"] == UserType.RIDER


class TestUserSearch:
    """Test user search functionality."""
    
    async def test_search_users_by_name(self, client: AsyncClient, auth_headers, db_session):
        """Test searching users by name."""
        # Create test users with profiles
        user1 = User(email="john@example.com", password="hash", user_type=UserType.CUSTOMER)
        user2 = User(email="jane@example.com", password="hash", user_type=UserType.CUSTOMER)
        
        db_session.add_all([user1, user2])
        await db_session.commit()
        
        params = {"query": "john", "limit": 10}
        response = await client.get("/users/search", params=params, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    async def test_search_users_empty_query(self, client: AsyncClient, auth_headers):
        """Test searching with empty query."""
        params = {"query": "", "limit": 10}
        response = await client.get("/users/search", params=params, headers=auth_headers)
        
        assert response.status_code == 400


class TestUserNotifications:
    """Test user notification preferences."""
    
    async def test_update_notification_preferences(self, client: AsyncClient, auth_headers):
        """Test updating notification preferences."""
        preferences = {
            "email_notifications": True,
            "push_notifications": False,
            "sms_notifications": True
        }
        
        response = await client.put("/users/notifications", json=preferences, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["email_notifications"] == preferences["email_notifications"]
        assert data["push_notifications"] == preferences["push_notifications"]
    
    async def test_update_notification_token(self, client: AsyncClient, auth_headers):
        """Test updating push notification token."""
        token_data = {
            "notification_token": "expo_push_token_123"
        }
        
        response = await client.post("/users/notification-token", json=token_data, headers=auth_headers)
        
        assert response.status_code == 200
        assert "token updated" in response.json()["message"].lower()


class TestUserValidation:
    """Test user data validation."""
    
    async def test_profile_validation_email_format(self, client: AsyncClient, auth_headers):
        """Test profile update with invalid email format."""
        update_data = {
            "email": "invalid-email-format"
        }
        
        response = await client.put("/users/profile", json=update_data, headers=auth_headers)
        assert response.status_code == 422
    
    async def test_profile_validation_phone_format(self, client: AsyncClient, auth_headers):
        """Test profile update with invalid phone format."""
        update_data = {
            "phone_number": "123"  # Too short
        }
        
        response = await client.put("/users/profile", json=update_data, headers=auth_headers)
        assert response.status_code == 422
    
    async def test_profile_validation_required_fields(self, client: AsyncClient, auth_headers):
        """Test profile update with missing required fields."""
        update_data = {}  # Empty update
        
        response = await client.put("/users/profile", json=update_data, headers=auth_headers)
        # Should still succeed as no fields are strictly required for update
        assert response.status_code == 200
