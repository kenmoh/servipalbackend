import asyncio
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text, select
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.database.database import get_db
from sqlalchemy.orm import sessionmaker
# Import models and use their Base
from app.models.models import Base, User, Profile, Wallet, Category, Item
from app.schemas.status_schema import UserType, AccountStatus
from app.schemas.item_schemas import ItemType, CategoryType
from app.config.config import settings
from app.services.auth_service import hash_password


# Use PostgreSQL test database from settings
from app.config.config import settings

test_engine = create_async_engine(
    url=settings.TEST_DATABASE_URL,
    echo=False,
    future=True
)


@pytest_asyncio.fixture
async def test_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for testing."""
    SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def override_get_db():
    """Override function for get_db dependency."""
    SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session

@pytest_asyncio.fixture(scope="session")
async def test_client():
    """Create test client."""
    # Setup: create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Override the get_db dependency
    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            yield client
    finally:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await test_engine.dispose()


@pytest_asyncio.fixture
async def test_customer(test_get_db):
    """Create test customer."""
    user = User(
        email="customer@example.com",
        password=hash_password("Password123!"),
        user_type=UserType.CUSTOMER,
        is_verified=True,
        is_email_verified=True,
        account_status=AccountStatus.CONFIRMED,
        updated_at=datetime.now(),
    )

    test_get_db.add(user)
    await test_get_db.flush()  # Ensure user.id is populated
    
    await test_get_db.commit()
    await test_get_db.refresh(user)
    return {
        "email": user.email,
        "password": user.password,
        "user_type": user.user_type,
        "phone_number": user.profile.phone_number if user.profile else None,
    }


@pytest_asyncio.fixture
async def test_restaurant_vendor(test_get_db):
    """Create test restaurant vendor."""
    user = User(
        email="restaurant@example.com",
        password=hash_password("Password123!"),
        user_type=UserType.RESTAURANT_VENDOR,
        is_verified=True,
        is_email_verified=True,
        account_status=AccountStatus.CONFIRMED,
        updated_at=datetime.now(),
    )

    test_get_db.add(user)
  
    await test_get_db.commit()
    await test_get_db.refresh(user)
    return {
        "email": user.email,
        "password": user.password,
        "user_type": user.user_type,
        "phone_number": user.profile.phone_number if user.profile else None,
    }

@pytest_asyncio.fixture
async def test_dispatch(test_get_db):
    """Create test dispatch."""
    user = User(
        email="dispatch@example.com",
        password=hash_password("Password123!"),
        user_type=UserType.DISPATCH,
        is_verified=True,
        is_email_verified=True,
        account_status=AccountStatus.CONFIRMED,
        updated_at=datetime.now(),
    )

    test_get_db.add(user)

    await test_get_db.commit()
    await test_get_db.refresh(user)
    return {
        "email": user.email,
        "password": user.password,
        "user_type": user.user_type,
        "phone_number": user.profile.phone_number if user.profile else None,
    }

@pytest_asyncio.fixture
async def test_laundry_vendort(test_get_db):
    """Create test laundry vendor."""
    user = User(
        email="laundry@example.com",
        password=hash_password("Password123!"),
        user_type=UserType.LAUNDRY_VENDOR,
        is_verified=True,
        is_email_verified=True,
        account_status=AccountStatus.CONFIRMED,
        updated_at=datetime.now(),
    )

    test_get_db.add(user)
    await test_get_db.commit()
    await test_get_db.refresh(user)
    return {
        "email": user.email,
        "password": user.password,
        "user_type": user.user_type,
        "phone_number": user.profile.phone_number if user.profile else None,
    }



@pytest_asyncio.fixture
async def test_rider(test_get_db, test_dispatch):
    """Create test rider (created by dispatch)."""
    # First get the dispatch user object
    from sqlalchemy import select
    dispatch_result = await test_get_db.execute(select(User).where(User.email == test_dispatch["email"]))
    dispatch_user = dispatch_result.scalar_one()
    
    user = User(
        email="rider@example.com",
        password=hash_password("Password123!"),
        user_type=UserType.RIDER,
        dispatcher_id=dispatch_user.id,
        is_verified=True,
        is_email_verified=True,
        account_status=AccountStatus.CONFIRMED,
        updated_at=datetime.now(),
    )
    test_get_db.add(user)
    await test_get_db.flush()
    
    # Create profile
    profile = Profile(
        user_id=user.id,
        full_name="Rider User",
        phone_number="+1234567895",
        is_phone_verified=True,
        bike_number="BIKE001",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        business_address=dispatch_user.profile.business_address if dispatch_user.profile else None,
        business_name=dispatch_user.profile.business_name if dispatch_user.profile else None,
    )
    test_get_db.add(profile)

    await test_get_db.commit()
    await test_get_db.refresh(user)
    return {
        "email": user.email,
        "password": user.password,
        "user_type": user.user_type,
        "phone_number": profile.phone_number,
    }



# Alias for backward compatibility
@pytest_asyncio.fixture
async def test_user(test_customer):
    """Alias for test_customer."""
    return test_customer


@pytest_asyncio.fixture
async def test_vendor(test_restaurant_vendor):
    """Alias for test_restaurant_vendor."""
    return test_restaurant_vendor


@pytest_asyncio.fixture
async def test_profile(test_get_db, test_user):
    """Create test profile."""
    # Get the user object
    from sqlalchemy import select
    user_result = await test_get_db.execute(select(User).where(User.email == test_user["email"]))
    user = user_result.scalar_one()
    
    # Check if profile already exists
    profile_result = await test_get_db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = profile_result.scalar_one_or_none()
    
    if not profile:
        # Create profile if it doesn't exist
        profile = Profile(
            user_id=user.id,
            full_name="Test User",
            phone_number="+1234567890",
            is_phone_verified=True,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        test_get_db.add(profile)
        await test_get_db.commit()
        await test_get_db.refresh(profile)
    
    return profile



@pytest_asyncio.fixture
async def test_category(test_get_db):
    """Create test category."""
    category = Category(
        id=uuid4(),
        name="Test Food",
        category_type=CategoryType.FOOD,
        created_at=datetime.now()
    )
    test_get_db.add(category)
    await test_get_db.commit()
    await test_get_db.refresh(category)
    return category


@pytest_asyncio.fixture
async def test_item(test_get_db, test_vendor, test_category):
    """Create test item."""
    # Get the vendor user object
    from sqlalchemy import select
    vendor_result = await test_get_db.execute(select(User).where(User.email == test_vendor["email"]))
    vendor_user = vendor_result.scalar_one()
    
    item = Item(
        id=uuid4(),
        name="Test Item",
        description="Test description",
        price=Decimal("25.00"),
        item_type=ItemType.FOOD,
        user_id=vendor_user.id,
        category_id=test_category.id,
        in_stock=True,
        stock=10,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    test_get_db.add(item)
    await test_get_db.commit()
    await test_get_db.refresh(item)
    return item


# Authentication fixtures for all user types
@pytest.fixture
def customer_auth_headers(test_customer):
    """Auth headers for customer."""
    from app.auth.auth import create_access_token
    token = create_access_token(data={"sub": test_customer.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def super_admin_auth_headers(test_super_admin):
    """Auth headers for super admin."""
    from app.auth.auth import create_access_token
    token = create_access_token(data={"sub": test_super_admin.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_headers(test_admin):
    """Auth headers for admin."""
    from app.auth.auth import create_access_token
    token = create_access_token(data={"sub": test_admin.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def moderator_auth_headers(test_moderator):
    """Auth headers for moderator."""
    from app.auth.auth import create_access_token
    token = create_access_token(data={"sub": test_moderator.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def dispatch_auth_headers(test_dispatch):
    """Auth headers for dispatch."""
    from app.auth.auth import create_access_token
    token = create_access_token(data={"sub": test_dispatch.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def rider_auth_headers(test_rider):
    """Auth headers for rider."""
    from app.auth.auth import create_access_token
    token = create_access_token(data={"sub": test_rider.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def restaurant_vendor_auth_headers(test_restaurant_vendor):
    """Auth headers for restaurant vendor."""
    from app.auth.auth import create_access_token
    token = create_access_token(data={"sub": test_restaurant_vendor.email})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def laundry_vendor_auth_headers(test_laundry_vendor):
    """Auth headers for laundry vendor."""
    from app.auth.auth import create_access_token
    token = create_access_token(data={"sub": test_laundry_vendor.email})
    return {"Authorization": f"Bearer {token}"}


# Alias for backward compatibility
@pytest.fixture
def auth_headers(customer_auth_headers):
    """Alias for customer_auth_headers."""
    return customer_auth_headers
