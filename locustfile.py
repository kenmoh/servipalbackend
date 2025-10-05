from locust import HttpUser, task, between
import json
import random


class APIUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        """Called when a user starts"""
        self.token = None
        self.user_id = None
        
    @task(1)
    def health_check(self):
        """Test health endpoint"""
        self.client.get("/")
        
    @task(2)
    def register_user(self):
        """Test user registration"""
        user_data = {
            "email": f"test{random.randint(1000, 9999)}@example.com",
            "password": "testpass123",
            "first_name": "Test",
            "last_name": "User",
            "phone_number": f"+234{random.randint(7000000000, 8999999999)}"
        }
        response = self.client.post("/api/auth/register", json=user_data)
        
    @task(3)
    def login_user(self):
        """Test user login"""
        login_data = {
            "username": "kenneth.aremoh@gmail.com",
            "password": "@String12"
        }
        response = self.client.post("/api/auth/login", data=login_data)
        if response.status_code == 200:
            data = response.json()
            self.token = data.get("access_token")
            
    @task(2)
    def get_products(self):
        """Test getting products"""
        self.client.get("/api/products/")
        
    @task(1)
    def get_marketplace_items(self):
        """Test marketplace items"""
        self.client.get("/api/marketplace/")
        
    def authenticated_request(self, method, url, **kwargs):
        """Helper for authenticated requests"""
        if self.token:
            headers = kwargs.get("headers", {})
            headers["Authorization"] = f"Bearer {self.token}"
            kwargs["headers"] = headers
        return getattr(self.client, method)(url, **kwargs)
        
    @task(1)
    def get_user_profile(self):
        """Test getting user profile (authenticated)"""
        if self.token:
            self.authenticated_request("get", "/api/users/me")
