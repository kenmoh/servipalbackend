
import asyncio
from decimal import Decimal
from copyreg import dispatch_table
import email
import io
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import text

from app import auth
from app.main import app
from app.models.models import Base, User, Profile, Wallet, ChargeAndCommission, Category
from app.database.database import get_db, create_test_session, create_test_engine
from app.config.config import settings
from app.schemas.item_schemas import ItemType
from app.schemas.status_schema import AccountStatus, UserType
from app.services.auth_service import hash_password


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create test engine once per session with proper connection settings."""
    engine = create_async_engine(
        settings.TEST_DATABASE_URL,
        poolclass=NullPool,
        echo=False,
        # Critical: Disable statement caching and use fresh connections
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "command_timeout": 60,
        }
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"TRUNCATE TABLE {table.name} RESTART IDENTITY CASCADE;"))

    yield engine
    
    # Cleanup
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_db(test_engine):
    """Create fresh database session for each test with transaction rollback."""
    # Create a new connection for each test to avoid type cache issues
    connection = await test_engine.connect()
    
    # Begin a transaction
    transaction = await connection.begin()
    
    # Create session bound to this connection
    async_session = async_sessionmaker(
        connection,
        expire_on_commit=False,
        class_=AsyncSession
    )
    
    session = async_session()
    
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def async_client(async_db):
    """Provides an async HTTP client that uses the test database session."""
    from app.database import database

    # Create a fresh test engine (same as in async_db setup)
    test_engine = create_test_engine()

    # Patch the global engine used by the app
    original_engine = getattr(database, 'engine', None)
    database.engine = test_engine

    # Also ensure the sessionmaker uses this engine
    test_session_maker = create_test_session(test_engine)
    original_session_maker = getattr(database, 'async_session_maker', None)
    database.async_session_maker = test_session_maker

    async def _get_test_db():
        yield async_db

    app.dependency_overrides[get_db] = _get_test_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client

    # Cleanup: restore original engine if needed (optional in tests)
    app.dependency_overrides.clear()
    if original_engine:
        await original_engine.dispose()
    if hasattr(database, 'engine') and database.engine:
        await database.engine.dispose()
    database.engine = original_engine
    database.async_session_maker = original_session_maker



import uuid


@pytest_asyncio.fixture
async def authenticated_user(async_client: AsyncClient, async_db: AsyncSession):
    """
    Fixture to create a customer user, log them in, and return authentication details.
    """
    unique_id = str(uuid.uuid4())[:8]
    user_payload = {
        "email": f"testuser_{unique_id}@example.com",
        "password": "Password123!",
        "user_type": UserType.CUSTOMER.value,
        "phone_number": f"+12345{unique_id[:5]}",
    }

    # Create user
    register_response = await async_client.post("/api/auth/register", json=user_payload)
    assert register_response.status_code == 201
    user_data = register_response.json()

    # Update profile with full name
    profile = await async_db.get(Profile, user_data["id"])
    profile.full_name = "Test User"
    await async_db.commit()
    await async_db.refresh(profile)

    # Log in user
    login_data = {"username": user_payload["email"], "password": user_payload["password"]}
    login_response = await async_client.post("/api/auth/login", data=login_data)
    assert login_response.status_code == 200
    auth_data = login_response.json()

    return {
        "user": user_data,
        "access_token": auth_data["access_token"],
        "refresh_token": auth_data["refresh_token"],
        "headers": {"Authorization": f"Bearer {auth_data['access_token']}"},
    }


@pytest_asyncio.fixture
async def authenticated_restaurant_vendor(async_client: AsyncClient):
    """
    Fixture to create a restaurant vendor, log them in, and return authentication details.
    """
    unique_id = str(uuid.uuid4())[:8]
    user_payload = {
        "email": f"restaurant_{unique_id}@example.com",
        "password": "Password123!",
        "user_type": UserType.RESTAURANT_VENDOR.value,
        "phone_number": f"+12346{unique_id[:5]}",
    }

    # Create user
    register_response = await async_client.post("/api/auth/register", json=user_payload)
    assert register_response.status_code == 201
    user_data = register_response.json()

    # Log in user
    login_data = {"username": user_payload["email"], "password": user_payload["password"]}
    login_response = await async_client.post("/api/auth/login", data=login_data)
    assert login_response.status_code == 200
    auth_data = login_response.json()

    return {
        "user": user_data,
        "access_token": auth_data["access_token"],
        "refresh_token": auth_data["refresh_token"],
        "headers": {"Authorization": f"Bearer {auth_data['access_token']}"},
    }


@pytest_asyncio.fixture
async def authenticated_laundry_vendor(async_client: AsyncClient):
    """
    Fixture to create a laundry vendor, log them in, and return authentication details.
    """
    unique_id = str(uuid.uuid4())[:8]
    user_payload = {
        "email": f"laundry_{unique_id}@example.com",
        "password": "Password123!",
        "user_type": UserType.LAUNDRY_VENDOR.value,
        "phone_number": f"+12347{unique_id[:5]}",
    }

    # Create user
    register_response = await async_client.post("/api/auth/register", json=user_payload)
    assert register_response.status_code == 201
    user_data = register_response.json()

    # Log in user
    login_data = {"username": user_payload["email"], "password": user_payload["password"]}
    login_response = await async_client.post("/api/auth/login", data=login_data)
    assert login_response.status_code == 200
    auth_data = login_response.json()

    return {
        "user": user_data,
        "access_token": auth_data["access_token"],
        "refresh_token": auth_data["refresh_token"],
        "headers": {"Authorization": f"Bearer {auth_data['access_token']}"},
    }


@pytest_asyncio.fixture
async def authenticated_dispatch_admin(async_client: AsyncClient, async_db: AsyncSession):
    """
    Fixture to create a dispatch admin, log them in, and return authentication details.
    """
    unique_id = str(uuid.uuid4())[:8]
    user_payload = {
        "email": f"dispatch_{unique_id}@example.com",
        "password": "Password123!",
        "user_type": UserType.DISPATCH.value,
        "phone_number": f"+12348{unique_id[:5]}",
    }

    new_dispatch_admin = User(
        email=user_payload["email"],
        user_type=UserType.DISPATCH.value,
        password=hash_password(user_payload["password"]),
        account_status=AccountStatus.CONFIRMED
    )

    async_db.add(new_dispatch_admin)
    await async_db.flush()

    wallet = Wallet(id=new_dispatch_admin.id, balance=0.0, escrow_balance=0.0)
    async_db.add(wallet)
   
    dispatch_profile = Profile(
        user_id=new_dispatch_admin.id,
        business_name=f"Dispatch Business {unique_id}",
        business_registration_number="RC675",
        phone_number=user_payload["phone_number"],
        business_address="123 Dispatch St, City, Country"
    )

    async_db.add(dispatch_profile)
    await async_db.commit()
    await async_db.refresh(new_dispatch_admin)


    # Log in user
    login_data = {"username": user_payload["email"], "password": user_payload["password"]}
    login_response = await async_client.post("/api/auth/login", data=login_data)

    assert login_response.status_code == 200
    auth_data = login_response.json()

    return {
     
        "access_token": auth_data["access_token"],
        "refresh_token": auth_data["refresh_token"],
        "token_type": auth_data["token_type"]
    }


@pytest_asyncio.fixture
async def authenticated_rider(authenticated_dispatch_admin, async_client: AsyncClient):
    """
    Fixture to create a rider (by a dispatch admin), log them in, and return authentication details.
    """
    unique_id = str(uuid.uuid4())[:8]
    
    rider_payload = {
        "email": f"rider_{unique_id}@example.com",
        "password": "Password123!",
        "user_type": UserType.RIDER.value,
        "phone_number": f"12349{unique_id[:5]}",
        "bike_number": f"BIKE{unique_id[:5]}",
        "business_name": authenticated_dispatch_admin.profile.business_name,
        "business_address": authenticated_dispatch_admin.profile.business_address,
        "full_name": f"Rider {unique_id}",
        "user_id": unique_id
    }

    # Create rider using dispatch admin's client
    # Assuming there's an endpoint for dispatch admin to create users
    # For now, we'll use the general register endpoint, but with admin's auth headers
    register_response = await async_client.post(
        "/api/auth/register",
        json=rider_payload,
        headers=authenticated_dispatch_admin["headers"]
    )
    assert register_response.status_code == 201
    assert register_response.json().get("email") == rider_payload["email"]
    rider_data = register_response.json()

    # Log in rider
    login_data = {"username": rider_payload["email"], "password": rider_payload["password"]}
    login_response = await async_client.post("/api/auth/login", data=login_data)
    assert login_response.status_code == 200
    auth_data = login_response.json()

    return {
        "user": rider_data,
        "access_token": auth_data["access_token"],
        "refresh_token": auth_data["refresh_token"],
        "headers": {"Authorization": f"Bearer {auth_data['access_token']}"},
    }


@pytest.fixture(scope="function")
async def create_charge_and_commission(async_db: AsyncClient):

    charge = ChargeAndCommission(
        payment_gate_way_fee=Decimal('0.14'),
        value_added_tax=Decimal('0.075'),
        payout_charge_transaction_upto_5000_naira=Decimal('10'),
        payout_charge_transaction_from_5001_to_50_000_naira=Decimal('25'),
        payout_charge_transaction_above_50_000_naira=Decimal('50'),
        stamp_duty=Decimal('50'),
        base_delivery_fee=Decimal('1500'),
        delivery_fee_per_km=Decimal('1500'),
        delivery_commission_percentage= Decimal('0.15'),
        food_laundry_commission_percentage=Decimal('0.10'),
        product_commission_percentage=Decimal('0.10')
    )

    async_db.add(charge)
    await async_db.commit()
    await async_db.refresh(charge)

    return charge


async def create_category(async_db: AsyncClient):
    category = Category(name='test_category')

    async_db.add(category)
    await async_db.commit(category)
    await async_db.refresh(category)
    return category

@pytest.fixture
async def create_menu(async_client: AsyncSession, authenticated_restaurant_vendor: User):
   
    headers = {"Authorization": f"Bearer {authenticated_user['access_token']}"}
    image_content= 'This is the image'
    category = await create_category()
    files = [("test.jpg", io.BytesIO(image_content), "image/jpeg"), ("test1.jpg", io.BytesIO(image_content), "image/jpeg")]
    menu_data = {
        "name":"Test Food Item",
        "description":"A delicious test food item",
        "price":Decimal("12.50"),
        "item_type":ItemType.FOOD,
        "category_id":category.id,
        "food_group": "main_course"
    }
    
    new_menu = await async_client.post('/api/items/menu-item-create', data=menu_data, headers=headers, files=files)
   
    return new_menu


@pytest.fixture(scope="function")
def event_loop():
    """Create event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()



