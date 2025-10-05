"""Test data factories for creating test objects."""
import factory
from factory import Faker, SubFactory
from decimal import Decimal
from datetime import datetime, timedelta
from uuid import uuid4

from app.models.models import User, Item, Order, Review, Product, Transaction
from app.schemas.status_schema import (
    UserType, AccountStatus, OrderStatus, PaymentStatus, 
    ItemType, CategoryType, FoodGroup, PaymentMethod,
    TransactionType, TransactionDirection
)


class UserFactory(factory.Factory):
    """Factory for creating User instances."""
    class Meta:
        model = User
    
    id = factory.LazyFunction(uuid4)
    email = Faker('email')
    password = factory.LazyFunction(lambda: "$2b$12$test_hashed_password")
    first_name = Faker('first_name')
    last_name = Faker('last_name')
    phone_number = Faker('phone_number')
    user_type = UserType.CUSTOMER
    is_verified = True
    is_email_verified = True
    account_status = AccountStatus.CONFIRMED
    created_at = factory.LazyFunction(datetime.utcnow)
    updated_at = factory.LazyFunction(datetime.utcnow)


class VendorFactory(UserFactory):
    """Factory for creating Vendor users."""
    user_type = UserType.RESTAURANT_VENDOR
    business_name = Faker('company')
    business_address = Faker('address')


class RiderFactory(UserFactory):
    """Factory for creating Rider users."""
    user_type = UserType.RIDER
    vehicle_type = "motorcycle"


class ProductFactory(factory.Factory):
    """Factory for creating Product instances."""
    class Meta:
        model = Product
    
    id = factory.LazyFunction(uuid4)
    name = Faker('word')
    description = Faker('text', max_nb_chars=200)
    price = factory.LazyFunction(lambda: Decimal(str(Faker('pydecimal', left_digits=3, right_digits=2, positive=True).generate())))
    category = CategoryType.FOOD
    is_available = True
    vendor = SubFactory(VendorFactory)
    created_at = factory.LazyFunction(datetime.utcnow)


class ItemFactory(factory.Factory):
    """Factory for creating Item instances."""
    class Meta:
        model = Item
    
    id = factory.LazyFunction(uuid4)
    name = Faker('word')
    description = Faker('text', max_nb_chars=200)
    price = factory.LazyFunction(lambda: Decimal(str(Faker('pydecimal', left_digits=3, right_digits=2, positive=True).generate())))
    item_type = ItemType.FOOD
    category = CategoryType.FOOD
    food_group = FoodGroup.MAIN_COURSE
    is_available = True
    vendor = SubFactory(VendorFactory)
    created_at = factory.LazyFunction(datetime.utcnow)


class OrderFactory(factory.Factory):
    """Factory for creating Order instances."""
    class Meta:
        model = Order
    
    id = factory.LazyFunction(uuid4)
    customer = SubFactory(UserFactory)
    vendor = SubFactory(VendorFactory)
    total_amount = factory.LazyFunction(lambda: Decimal(str(Faker('pydecimal', left_digits=3, right_digits=2, positive=True).generate())))
    delivery_fee = Decimal('5.00')
    service_fee = Decimal('2.50')
    status = OrderStatus.PENDING
    payment_status = PaymentStatus.PENDING
    payment_method = PaymentMethod.CARD
    delivery_address = Faker('address')
    created_at = factory.LazyFunction(datetime.utcnow)


class ReviewFactory(factory.Factory):
    """Factory for creating Review instances."""
    class Meta:
        model = Review
    
    id = factory.LazyFunction(uuid4)
    reviewer = SubFactory(UserFactory)
    reviewee = SubFactory(VendorFactory)
    order = SubFactory(OrderFactory)
    rating = factory.LazyFunction(lambda: Faker('random_int', min=1, max=5).generate())
    comment = Faker('text', max_nb_chars=500)
    created_at = factory.LazyFunction(datetime.utcnow)


class TransactionFactory(factory.Factory):
    """Factory for creating Transaction instances."""
    class Meta:
        model = Transaction
    
    id = factory.LazyFunction(uuid4)
    user = SubFactory(UserFactory)
    amount = factory.LazyFunction(lambda: Decimal(str(Faker('pydecimal', left_digits=3, right_digits=2, positive=True).generate())))
    transaction_type = TransactionType.PAYMENT
    direction = TransactionDirection.DEBIT
    status = PaymentStatus.COMPLETED
    reference = Faker('uuid4')
    created_at = factory.LazyFunction(datetime.utcnow)
