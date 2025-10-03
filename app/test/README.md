# ServiPal Backend Tests

This directory contains comprehensive tests for the ServiPal backend application using pytest and best practices.

## Test Structure

```
app/test/
├── conftest.py              # Test configuration and fixtures
├── test_auth.py            # Authentication tests
├── test_users.py           # User management tests
├── test_orders.py          # Order management tests
├── test_items.py           # Item/product tests
├── test_integration.py     # Integration tests
├── test_services.py        # Service layer tests
└── README.md              # This file
```

## Running Tests

### Basic Usage

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest app/test/test_auth.py

# Run specific test class
pytest app/test/test_auth.py::TestAuthRegistration

# Run specific test method
pytest app/test/test_auth.py::TestAuthRegistration::test_register_customer_success
```

### Using the Test Runner Script

```bash
# Run all tests with coverage
python run_tests.py --coverage

# Run only unit tests
python run_tests.py --unit

# Run only integration tests
python run_tests.py --integration

# Run auth-related tests
python run_tests.py --auth

# Run specific test file
python run_tests.py --file test_auth.py

# Verbose output
python run_tests.py --verbose
```

### Test Categories

Tests are organized by markers:

- `@pytest.mark.unit` - Unit tests (fast, isolated)
- `@pytest.mark.integration` - Integration tests (slower, multiple components)
- `@pytest.mark.auth` - Authentication-related tests
- `@pytest.mark.orders` - Order management tests
- `@pytest.mark.users` - User management tests
- `@pytest.mark.items` - Item/product tests

## Test Configuration

### Environment Variables

Tests use a separate test database configuration. Set these environment variables:

```bash
TEST_DATABASE_URL=sqlite+aiosqlite:///:memory:
ENVIRONMENT=testing
JWT_SECRET_KEY=test_secret_key
```

### Fixtures

Common fixtures available in all tests:

- `client` - HTTP test client
- `db_session` - Database session
- `test_user` - Sample customer user
- `test_vendor` - Sample vendor user
- `test_rider` - Sample rider user
- `auth_headers` - Authorization headers for test user
- `vendor_auth_headers` - Authorization headers for vendor
- `rider_auth_headers` - Authorization headers for rider

## Test Best Practices

### 1. Test Naming

```python
async def test_create_order_success(self, client, auth_headers):
    """Test successful order creation."""
    # Test implementation
```

### 2. Test Organization

```python
class TestOrderCreation:
    """Test order creation endpoints."""
    
    async def test_create_order_success(self, ...):
        """Test successful order creation."""
        
    async def test_create_order_invalid_item(self, ...):
        """Test order creation with invalid item."""
```

### 3. Mocking External Services

```python
async def test_send_email_notification(self, client, auth_headers):
    """Test email notification sending."""
    with patch('app.services.email_service.send_email', new_callable=AsyncMock) as mock_send:
        response = await client.post("/send-notification", headers=auth_headers)
        mock_send.assert_called_once()
```

### 4. Database Testing

```python
async def test_create_user_success(self, db_session):
    """Test user creation in database."""
    user = User(email="test@example.com", password="hashed")
    db_session.add(user)
    await db_session.commit()
    
    # Verify user was created
    assert user.id is not None
```

### 5. API Testing

```python
async def test_get_user_profile(self, client, auth_headers):
    """Test getting user profile via API."""
    response = await client.get("/users/profile", headers=auth_headers)
    
    assert response.status_code == 200
    data = response.json()
    assert "email" in data
```

## Coverage Goals

- **Minimum Coverage**: 70%
- **Target Coverage**: 85%
- **Critical Paths**: 95%+ (auth, payments, orders)

## Continuous Integration

Tests are configured to run in CI/CD pipelines:

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    python -m pytest --cov=app --cov-report=xml
    
- name: Upload coverage
  uses: codecov/codecov-action@v1
```

## Common Test Patterns

### Testing Authentication

```python
async def test_protected_endpoint(self, client, auth_headers):
    """Test accessing protected endpoint."""
    response = await client.get("/protected", headers=auth_headers)
    assert response.status_code == 200

async def test_protected_endpoint_unauthorized(self, client):
    """Test accessing protected endpoint without auth."""
    response = await client.get("/protected")
    assert response.status_code == 401
```

### Testing Database Operations

```python
async def test_create_and_retrieve(self, db_session):
    """Test creating and retrieving data."""
    # Create
    item = Item(name="Test", price=10.99)
    db_session.add(item)
    await db_session.commit()
    
    # Retrieve
    retrieved = await db_session.get(Item, item.id)
    assert retrieved.name == "Test"
```

### Testing Error Handling

```python
async def test_handle_database_error(self, client, auth_headers):
    """Test handling of database errors."""
    with patch('app.database.database.get_db', side_effect=Exception("DB Error")):
        response = await client.get("/users/profile", headers=auth_headers)
        assert response.status_code == 500
```

## Debugging Tests

### Running Single Test with Debug

```bash
pytest -v -s app/test/test_auth.py::TestAuthRegistration::test_register_customer_success
```

### Using pdb for Debugging

```python
async def test_debug_example(self, client):
    """Test with debugging."""
    import pdb; pdb.set_trace()
    response = await client.get("/debug")
    assert response.status_code == 200
```

### Viewing Test Output

```bash
# Show print statements
pytest -s

# Show detailed output
pytest -v

# Show coverage report
pytest --cov=app --cov-report=term-missing
```

## Performance Testing

For performance-critical endpoints:

```python
import time

async def test_endpoint_performance(self, client, auth_headers):
    """Test endpoint response time."""
    start_time = time.time()
    response = await client.get("/fast-endpoint", headers=auth_headers)
    end_time = time.time()
    
    assert response.status_code == 200
    assert (end_time - start_time) < 1.0  # Should respond within 1 second
```

## Test Data Management

### Using Factories

```python
def create_test_user(email="test@example.com", user_type=UserType.CUSTOMER):
    """Factory function for creating test users."""
    return User(
        email=email,
        password=get_password_hash("testpassword"),
        user_type=user_type,
        is_verified=True
    )
```

### Cleanup

Tests automatically clean up using fixtures, but for manual cleanup:

```python
async def test_with_cleanup(self, db_session):
    """Test with manual cleanup."""
    user = User(email="temp@example.com", password="hash")
    db_session.add(user)
    await db_session.commit()
    
    try:
        # Test logic here
        pass
    finally:
        await db_session.delete(user)
        await db_session.commit()
```
