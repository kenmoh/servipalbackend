"""
Locust configuration for load testing
"""

# Test configuration
HOST = "http://localhost:8000"  # Local API host
USERS = 10  # Number of concurrent users
SPAWN_RATE = 2  # Users spawned per second
RUN_TIME = "5m"  # Test duration

# Web UI configuration
WEB_HOST = "0.0.0.0"
WEB_PORT = 8089

# Test scenarios
SCENARIOS = {
    "light_load": {"users": 5, "spawn_rate": 1, "run_time": "2m"},
    "medium_load": {"users": 20, "spawn_rate": 5, "run_time": "5m"},
    "heavy_load": {"users": 50, "spawn_rate": 10, "run_time": "10m"},
    "production_load": {"users": 1000, "spawn_rate": 100, "run_time": "5m"},
    "auth_test": {"users": 10, "spawn_rate": 5, "run_time": "2m"},
    "extreme_load": {"users": 10000, "spawn_rate": 300, "run_time": "2m"},
}
