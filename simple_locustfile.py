from locust import HttpUser, task, between


class SimpleAPIUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(10)
    def health_check(self):
        """Test health endpoint"""
        self.client.get("/")
        
    @task(1)
    def docs_endpoint(self):
        """Test docs endpoint"""
        self.client.get("/docs")
        
    @task(1)
    def openapi_endpoint(self):
        """Test OpenAPI schema endpoint"""
        self.client.get("/openapi.json")
