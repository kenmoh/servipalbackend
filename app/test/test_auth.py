import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock

from app.models.models import User
from app.schemas.status_schema import UserType


class TestAuthRegistration:
    """Test user registration endpoints."""
    
    async def test_register_customer_success(self, client: AsyncClient):
        """Test successful customer registration."""
        user_data = {
            "email": "newuser@example.com",
            "password": "SecurePass123!",
            "user_type": "customer"
        }
        
        with patch('app.services.auth_service.send_verification_email', new_callable=AsyncMock):
            response = await client.post("/auth/register", json=user_data)
        
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == user_data["email"]
        assert "password" not in data
        assert data["user_type"] == UserType.CUSTOMER
    
    async def test_register_duplicate_email(self, client: AsyncClient, test_user):
        """Test registration with existing email."""
        user_data = {
            "email": test_user.email,
            "password": "SecurePass123!",
            "user_type": "customer"
        }
        
        response = await client.post("/auth/register", json=user_data)
        assert response.status_code == 400
        assert "already registered" in response.json()["detail"].lower()
    
    async def test_register_invalid_email(self, client: AsyncClient):
        """Test registration with invalid email."""
        user_data = {
            "email": "invalid-email",
            "password": "SecurePass123!",
            "user_type": "customer"
        }
        
        response = await client.post("/auth/register", json=user_data)
        assert response.status_code == 422
    
    async def test_register_weak_password(self, client: AsyncClient):
        """Test registration with weak password."""
        user_data = {
            "email": "test@example.com",
            "password": "123",
            "user_type": "customer"
        }
        
        response = await client.post("/auth/register", json=user_data)
        assert response.status_code == 422


class TestAuthLogin:
    """Test user login endpoints."""
    
    async def test_login_success(self, client: AsyncClient, test_user):
        """Test successful login."""
        login_data = {
            "username": test_user.email,
            "password": "testpassword123"
        }
        
        response = await client.post("/auth/login", data=login_data)
        assert response.status_code == 200
        
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
    
    async def test_login_invalid_credentials(self, client: AsyncClient, test_user):
        """Test login with invalid credentials."""
        login_data = {
            "username": test_user.email,
            "password": "wrongpassword"
        }
        
        response = await client.post("/auth/login", data=login_data)
        assert response.status_code == 401
        assert "incorrect" in response.json()["detail"].lower()
    
    async def test_login_nonexistent_user(self, client: AsyncClient):
        """Test login with non-existent user."""
        login_data = {
            "username": "nonexistent@example.com",
            "password": "password123"
        }
        
        response = await client.post("/auth/login", data=login_data)
        assert response.status_code == 401


class TestAuthTokens:
    """Test token-related endpoints."""
    
    async def test_refresh_token_success(self, client: AsyncClient, test_user):
        """Test successful token refresh."""
        # First login to get refresh token
        login_data = {
            "username": test_user.email,
            "password": "testpassword123"
        }
        
        login_response = await client.post("/auth/login", data=login_data)
        refresh_token = login_response.json()["refresh_token"]
        
        # Use refresh token
        response = await client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
    
    async def test_refresh_token_invalid(self, client: AsyncClient):
        """Test refresh with invalid token."""
        response = await client.post(
            "/auth/refresh",
            json={"refresh_token": "invalid_token"}
        )
        
        assert response.status_code == 401


class TestAuthProtectedRoutes:
    """Test protected route access."""
    
    async def test_protected_route_with_valid_token(self, client: AsyncClient, auth_headers):
        """Test accessing protected route with valid token."""
        response = await client.get("/users/profile", headers=auth_headers)
        assert response.status_code == 200
    
    async def test_protected_route_without_token(self, client: AsyncClient):
        """Test accessing protected route without token."""
        response = await client.get("/users/profile")
        assert response.status_code == 401
    
    async def test_protected_route_with_invalid_token(self, client: AsyncClient):
        """Test accessing protected route with invalid token."""
        headers = {"Authorization": "Bearer invalid_token"}
        response = await client.get("/users/profile", headers=headers)
        assert response.status_code == 401


class TestEmailVerification:
    """Test email verification endpoints."""
    
    async def test_verify_email_success(self, client: AsyncClient, db_session):
        """Test successful email verification."""
        # Create unverified user
        user = User(
            email="unverified@example.com",
            password="hashedpassword",
            is_email_verified=False,
            email_verification_code="123456"
        )
        db_session.add(user)
        await db_session.commit()
        
        response = await client.post(
            "/auth/verify-email",
            json={
                "email": user.email,
                "verification_code": "123456"
            }
        )
        
        assert response.status_code == 200
        assert "verified successfully" in response.json()["message"].lower()
    
    async def test_verify_email_invalid_code(self, client: AsyncClient, test_user):
        """Test email verification with invalid code."""
        response = await client.post(
            "/auth/verify-email",
            json={
                "email": test_user.email,
                "verification_code": "wrong_code"
            }
        )
        
        assert response.status_code == 400


class TestPasswordReset:
    """Test password reset functionality."""
    
    async def test_request_password_reset(self, client: AsyncClient, test_user):
        """Test password reset request."""
        with patch('app.services.auth_service.send_password_reset_email', new_callable=AsyncMock):
            response = await client.post(
                "/auth/forgot-password",
                json={"email": test_user.email}
            )
        
        assert response.status_code == 200
        assert "reset link sent" in response.json()["message"].lower()
    
    async def test_request_password_reset_nonexistent_email(self, client: AsyncClient):
        """Test password reset request for non-existent email."""
        response = await client.post(
            "/auth/forgot-password",
            json={"email": "nonexistent@example.com"}
        )
        
        # Should still return 200 for security (don't reveal if email exists)
        assert response.status_code == 200
