# Load Testing Setup

This project uses Locust for load testing with Flower for monitoring.

## Installation

Install dependencies:
```bash
uv sync
```

## Running Load Tests

### Option 1: Using the runner script
```bash
# Medium load test with web UI
python run_load_test.py --scenario medium_load

# Heavy load test in headless mode
python run_load_test.py --scenario heavy_load --headless

# Light load test
python run_load_test.py --scenario light_load
```

### Option 2: Direct Locust commands
```bash
# With web UI (recommended for development)
locust -f locustfile.py --host http://localhost:8000

# Headless mode
locust -f locustfile.py --host http://localhost:8000 --headless -u 10 -r 2 -t 5m
```

## Web Interface

When running with web UI, access:
- Locust Web UI: http://localhost:8089

## Test Scenarios

- **light_load**: 5 users, 1 spawn/sec, 2 minutes
- **medium_load**: 20 users, 5 spawn/sec, 5 minutes  
- **heavy_load**: 50 users, 10 spawn/sec, 10 minutes

## Configuration

Edit `load_test_config.py` to modify:
- Target host
- User counts
- Test duration
- Spawn rates

## Test Coverage

Current tests cover:
- Health check endpoint
- User registration
- User login
- Product listing
- Marketplace items
- Authenticated user profile

Add more test scenarios in `locustfile.py` as needed.
