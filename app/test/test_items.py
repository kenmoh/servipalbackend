import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import uuid4
from decimal import Decimal

from app.models.models import Item, User
from app.schemas.item_schemas import CategoryType, ItemType
from app.schemas.status_schema import UserType


class TestItemCreation:
    """Test item creation endpoints."""
    
    async def test_create_item_success(self, client: AsyncClient, vendor_auth_headers):
        """Test successful item creation by vendor."""
        item_data = {
            "name": "Margherita Pizza",
            "description": "Classic pizza with tomato and mozzarella",
            "price": 15.99,
            "category": "food",
            "item_type": "main_course",
            "is_available": True,
            "preparation_time": 20
        }
        
        with patch('app.utils.s3_service.upload_file', return_value="https://s3.amazonaws.com/image.jpg"):
            response = await client.post("/items", json=item_data, headers=vendor_auth_headers)
        
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == item_data["name"]
        assert data["price"] == item_data["price"]
        assert data["is_available"] == item_data["is_available"]
    
    async def test_create_item_customer_forbidden(self, client: AsyncClient, auth_headers):
        """Test that customers cannot create items."""
        item_data = {
            "name": "Test Item",
            "price": 10.99,
            "category": "food"
        }
        
        response = await client.post("/items", json=item_data, headers=auth_headers)
        assert response.status_code == 403
        assert "permission" in response.json()["detail"].lower()
    
    async def test_create_item_invalid_price(self, client: AsyncClient, vendor_auth_headers):
        """Test creating item with invalid price."""
        item_data = {
            "name": "Test Item",
            "price": -5.99,  # Negative price
            "category": "food"
        }
        
        response = await client.post("/items", json=item_data, headers=vendor_auth_headers)
        assert response.status_code == 422
    
    async def test_create_item_missing_required_fields(self, client: AsyncClient, vendor_auth_headers):
        """Test creating item with missing required fields."""
        item_data = {
            "description": "Missing name and price"
        }
        
        response = await client.post("/items", json=item_data, headers=vendor_auth_headers)
        assert response.status_code == 422


class TestItemRetrieval:
    """Test item retrieval endpoints."""
    
    async def test_get_all_items(self, client: AsyncClient):
        """Test getting all available items."""
        response = await client.get("/items")
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    async def test_get_item_by_id_success(self, client: AsyncClient, db_session, test_vendor):
        """Test getting specific item by ID."""
        item = Item(
            id=uuid4(),
            name="Test Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            category=CategoryType.FOOD
        )
        db_session.add(item)
        await db_session.commit()
        
        response = await client.get(f"/items/{item.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(item.id)
        assert data["name"] == item.name
    
    async def test_get_item_by_id_not_found(self, client: AsyncClient):
        """Test getting non-existent item."""
        fake_id = uuid4()
        response = await client.get(f"/items/{fake_id}")
        
        assert response.status_code == 404
        assert "item not found" in response.json()["detail"].lower()
    
    async def test_get_vendor_items(self, client: AsyncClient, vendor_auth_headers, db_session, test_vendor):
        """Test getting items for specific vendor."""
        # Create items for vendor
        items = [
            Item(
                id=uuid4(),
                name="Pizza 1",
                price=Decimal("15.99"),
                vendor_id=test_vendor.id,
                is_available=True,
                category=CategoryType.FOOD
            ),
            Item(
                id=uuid4(),
                name="Pizza 2",
                price=Decimal("18.99"),
                vendor_id=test_vendor.id,
                is_available=True,
                category=CategoryType.FOOD
            )
        ]
        db_session.add_all(items)
        await db_session.commit()
        
        response = await client.get("/items/my-items", headers=vendor_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        assert all(item["vendor_id"] == str(test_vendor.id) for item in data)


class TestItemUpdates:
    """Test item update endpoints."""
    
    async def test_update_item_success(self, client: AsyncClient, vendor_auth_headers, db_session, test_vendor):
        """Test successful item update by owner."""
        item = Item(
            id=uuid4(),
            name="Original Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            category=CategoryType.FOOD
        )
        db_session.add(item)
        await db_session.commit()
        
        update_data = {
            "name": "Updated Pizza",
            "price": 18.99,
            "description": "Updated description"
        }
        
        response = await client.put(f"/items/{item.id}", json=update_data, headers=vendor_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == update_data["name"]
        assert data["price"] == update_data["price"]
    
    async def test_update_item_not_owner(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test updating item by non-owner."""
        item = Item(
            id=uuid4(),
            name="Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            category=CategoryType.FOOD
        )
        db_session.add(item)
        await db_session.commit()
        
        update_data = {
            "name": "Hacked Pizza"
        }
        
        response = await client.put(f"/items/{item.id}", json=update_data, headers=auth_headers)
        
        assert response.status_code == 403
        assert "permission" in response.json()["detail"].lower()
    
    async def test_toggle_item_availability(self, client: AsyncClient, vendor_auth_headers, db_session, test_vendor):
        """Test toggling item availability."""
        item = Item(
            id=uuid4(),
            name="Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            category=CategoryType.FOOD
        )
        db_session.add(item)
        await db_session.commit()
        
        response = await client.post(f"/items/{item.id}/toggle-availability", headers=vendor_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["is_available"] == False  # Should be toggled


class TestItemDeletion:
    """Test item deletion endpoints."""
    
    async def test_delete_item_success(self, client: AsyncClient, vendor_auth_headers, db_session, test_vendor):
        """Test successful item deletion by owner."""
        item = Item(
            id=uuid4(),
            name="Pizza to Delete",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            category=CategoryType.FOOD
        )
        db_session.add(item)
        await db_session.commit()
        
        response = await client.delete(f"/items/{item.id}", headers=vendor_auth_headers)
        
        assert response.status_code == 204
    
    async def test_delete_item_not_owner(self, client: AsyncClient, auth_headers, db_session, test_vendor):
        """Test deleting item by non-owner."""
        item = Item(
            id=uuid4(),
            name="Pizza",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            category=CategoryType.FOOD
        )
        db_session.add(item)
        await db_session.commit()
        
        response = await client.delete(f"/items/{item.id}", headers=auth_headers)
        
        assert response.status_code == 403
    
    async def test_delete_item_with_active_orders(self, client: AsyncClient, vendor_auth_headers, db_session, test_vendor):
        """Test deleting item that has active orders."""
        item = Item(
            id=uuid4(),
            name="Pizza with Orders",
            price=Decimal("15.99"),
            vendor_id=test_vendor.id,
            is_available=True,
            category=CategoryType.FOOD
        )
        db_session.add(item)
        await db_session.commit()
        
        # Mock having active orders
        with patch('app.services.item_service.has_active_orders', return_value=True):
            response = await client.delete(f"/items/{item.id}", headers=vendor_auth_headers)
        
        assert response.status_code == 400
        assert "active orders" in response.json()["detail"].lower()


class TestItemSearch:
    """Test item search and filtering."""
    
    async def test_search_items_by_name(self, client: AsyncClient, db_session, test_vendor):
        """Test searching items by name."""
        # Create test items
        items = [
            Item(
                id=uuid4(),
                name="Margherita Pizza",
                price=Decimal("15.99"),
                vendor_id=test_vendor.id,
                is_available=True,
                category=CategoryType.FOOD
            ),
            Item(
                id=uuid4(),
                name="Pepperoni Pizza",
                price=Decimal("18.99"),
                vendor_id=test_vendor.id,
                is_available=True,
                category=CategoryType.FOOD
            )
        ]
        db_session.add_all(items)
        await db_session.commit()
        
        params = {"search": "pizza"}
        response = await client.get("/items/search", params=params)
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        assert all("pizza" in item["name"].lower() for item in data)
    
    async def test_filter_items_by_category(self, client: AsyncClient, db_session, test_vendor):
        """Test filtering items by category."""
        # Create items with different categories
        items = [
            Item(
                id=uuid4(),
                name="Pizza",
                price=Decimal("15.99"),
                vendor_id=test_vendor.id,
                is_available=True,
                category=CategoryType.FOOD
            ),
            Item(
                id=uuid4(),
                name="Coke",
                price=Decimal("2.99"),
                vendor_id=test_vendor.id,
                is_available=True,
                category=CategoryType.BEVERAGE
            )
        ]
        db_session.add_all(items)
        await db_session.commit()
        
        params = {"category": "food"}
        response = await client.get("/items", params=params)
        
        assert response.status_code == 200
        data = response.json()
        assert all(item["category"] == CategoryType.FOOD for item in data)
    
    async def test_filter_items_by_price_range(self, client: AsyncClient):
        """Test filtering items by price range."""
        params = {
            "min_price": 10.00,
            "max_price": 20.00
        }
        
        response = await client.get("/items", params=params)
        
        assert response.status_code == 200
        data = response.json()
        assert all(10.00 <= item["price"] <= 20.00 for item in data)
    
    async def test_filter_available_items_only(self, client: AsyncClient):
        """Test filtering only available items."""
        params = {"available_only": True}
        
        response = await client.get("/items", params=params)
        
        assert response.status_code == 200
        data = response.json()
        assert all(item["is_available"] for item in data)


class TestItemValidation:
    """Test item data validation."""
    
    async def test_item_name_length_validation(self, client: AsyncClient, vendor_auth_headers):
        """Test item name length validation."""
        item_data = {
            "name": "A" * 256,  # Too long
            "price": 15.99,
            "category": "food"
        }
        
        response = await client.post("/items", json=item_data, headers=vendor_auth_headers)
        assert response.status_code == 422
    
    async def test_item_price_precision_validation(self, client: AsyncClient, vendor_auth_headers):
        """Test item price precision validation."""
        item_data = {
            "name": "Test Item",
            "price": 15.999999,  # Too many decimal places
            "category": "food"
        }
        
        response = await client.post("/items", json=item_data, headers=vendor_auth_headers)
        # Should either accept and round, or reject
        assert response.status_code in [201, 422]
    
    async def test_item_category_validation(self, client: AsyncClient, vendor_auth_headers):
        """Test item category validation."""
        item_data = {
            "name": "Test Item",
            "price": 15.99,
            "category": "invalid_category"
        }
        
        response = await client.post("/items", json=item_data, headers=vendor_auth_headers)
        assert response.status_code == 422
