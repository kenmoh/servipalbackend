from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import secrets
from uuid import UUID, uuid4
import uuid
from datetime import datetime
from decimal import Decimal
import logging
from typing import Optional, Tuple

from sqlalchemy import DateTime, ForeignKey, ARRAY, String, func, Float, text, Identity, Integer, UniqueConstraint
from sqlalchemy.schema import Sequence
from sqlalchemy.dialects.postgresql import CHAR
from sqlalchemy.orm import mapped_column, Mapped, relationship
from sqlalchemy.types import TypeDecorator

from app.database.database import Base
from app.schemas.delivery_schemas import DeliveryType
from app.schemas.item_schemas import ItemType
from app.schemas.status_schema import (
    AccountStatus,
    DelivertyStatus,
    OrderStatus,
    OrderType,
    PaymentStatus,
    RequireDeliverySchema,
    TransactionType,
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


# def generate_order_number():
#     characters = string.ascii_uppercase + string.digits
#     return "".join(random.choice(characters) for _ in range(10))


def generate_order_number():
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = secrets.token_hex(3).upper()[:5]
    return f"ORD-{date_part}-{random_part}"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(unique=True, nullable=False)
    password: Mapped[str]
    is_blocked: Mapped[bool] = mapped_column(default=False)
    is_verified: Mapped[bool] = mapped_column(default=False)
    reset_token: Mapped[Optional[str]] = mapped_column(
        nullable=True, unique=True)
    reset_token_expires: Mapped[Optional[datetime]
                                ] = mapped_column(nullable=True)
    user_type: Mapped[str] = mapped_column(  # make it a UserType Enum
        nullable=False,
    )

    is_email_verified: Mapped[bool] = mapped_column(default=False, nullable=True)
    email_verification_code: Mapped[str] = mapped_column(nullable=True)
    email_verification_expires: Mapped[datetime] = mapped_column(
        nullable=True)
    account_status: Mapped[AccountStatus] = mapped_column(
        default=AccountStatus.PENDING)
    # Add dispatcher-rider relationship
    dispatcher_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )

    # Dispatcher-side
    managed_riders: Mapped[list["User"]] = relationship(
        "User",
        back_populates="dispatcher",
        foreign_keys=[dispatcher_id],
        # remote_side=[id],
        lazy="dynamic",  # Important for self-referential relationship
    )
    # Rider-side

    dispatcher: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="managed_riders",
        foreign_keys=[dispatcher_id],
        remote_side=[id],
        lazy="joined",
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    # Relationships
    profile: Mapped["Profile"] = relationship(
        back_populates="user",
        uselist=False,
        lazy="selectin",
    )
    wallet: Mapped["Wallet"] = relationship(
        back_populates="user",
        uselist=False,
        lazy="selectin",
    )
    items: Mapped[list["Item"]] = relationship(back_populates="vendor")
    orders_placed: Mapped[list["Order"]] = relationship(
        back_populates="owner", foreign_keys="Order.owner_id"
    )
    orders_received: Mapped[list["Order"]] = relationship(
        back_populates="vendor", foreign_keys="Order.vendor_id"
    )
    deliveries_as_rider: Mapped[list["Delivery"]] = relationship(
        back_populates="rider", foreign_keys="Delivery.rider_id"
    )
    deliveries_as_sender: Mapped[list["Delivery"]] = relationship(
        back_populates="sender", foreign_keys="Delivery.sender_id"
    )
    # products_listed: Mapped[list["Product"]] = relationship(
    #     back_populates="seller", foreign_keys="Product.seller_id"
    # )
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )


class Profile(Base):
    __tablename__ = "profile"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    business_name: Mapped[str] = mapped_column(nullable=True)
    bank_name: Mapped[str] = mapped_column(nullable=True)
    bank_account_number: Mapped[str] = mapped_column(nullable=True)
    business_address: Mapped[str] = mapped_column(nullable=True)
    business_registration_number: Mapped[str] = mapped_column(nullable=True)
    opening_hours: Mapped[datetime] = mapped_column(nullable=True)
    closing_hours: Mapped[datetime] = mapped_column(nullable=True)
    full_name: Mapped[str] = mapped_column(nullable=True)
    phone_number: Mapped[str] = mapped_column(unique=True, nullable=False)
    bike_number: Mapped[str] = mapped_column(unique=True, nullable=True)
    is_phone_verified: Mapped[bool] = mapped_column(default=False, nullable=True)
    phone_verification_code: Mapped[str] = mapped_column(nullable=True)
    phone_verification_expires: Mapped[datetime] = mapped_column(
        nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    user: Mapped["User"] = relationship(back_populates="profile")
    profile_image: Mapped["ProfileImage"] = relationship(
        back_populates="profile",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    backdrop: Mapped["ProfileBackdrop"] = relationship(
        back_populates="profile",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class ProfileImage(Base):
    __tablename__ = "profile_images"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("profile.id", ondelete="CASCADE"))
    url: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now)

    profile: Mapped["Profile"] = relationship(back_populates="profile_image")


class ProfileBackdrop(Base):
    __tablename__ = "profile_backdrops"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("profile.id", ondelete="CASCADE"))
    url: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now)

    profile: Mapped["Profile"] = relationship(back_populates="backdrop")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    terminated_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    device_info: Mapped[str]
    ip_address: Mapped[str]
    last_active: Mapped[datetime] = mapped_column(default=datetime.now)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    termination_reason: Mapped[str] = mapped_column(nullable=True)
    terminated_by_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[terminated_by],
        backref="terminated_sessions"
    )


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    balance: Mapped[Decimal] = mapped_column(default=0.00)
    escrow_balance: Mapped[Decimal] = mapped_column(default=0.00)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    user: Mapped["User"] = relationship(back_populates="wallet")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="wallet", lazy="selectin"
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    wallet_id: Mapped[UUID] = mapped_column(ForeignKey("wallets.id"))
    amount: Mapped[Decimal] = mapped_column(default=0.00)
    transaction_type: Mapped[TransactionType]
    payment_status: Mapped[PaymentStatus] = mapped_column(
        default=PaymentStatus.PENDING)
    payment_link: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )
    wallet: Mapped["Wallet"] = relationship(back_populates="transactions")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(
        primary_key=True, nullable=False, default=uuid.uuid1, index=True
    )
    token: Mapped[str] = mapped_column(unique=True, index=True, nullable=False)
    user_type: Mapped[str] = mapped_column(
        nullable=True)  # TODO: change to False
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user = relationship("User", back_populates="refresh_tokens")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    items: Mapped[list["Item"]] = relationship(back_populates="category")
    # products: Mapped[list["Product"]] = relationship(back_populates="category")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    item_type: Mapped[ItemType]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str]
    description: Mapped[str] = mapped_column(nullable=True)
    price: Mapped[Decimal] = mapped_column(default=0.00, nullable=False)
    sizes: Mapped[str] = mapped_column(nullable=True)
    colors: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    stock: Mapped[int] = mapped_column(nullable=True)
    in_stock: Mapped[bool] = mapped_column(default=True)
    total_sold: Mapped[int] = mapped_column(nullable=True)

    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    vendor: Mapped["User"] = relationship(back_populates="items")
    category: Mapped["Category"] = relationship(back_populates="items")
    order_items: Mapped[list["OrderItem"]
                        ] = relationship(back_populates="item")
    images: Mapped[list["ItemImage"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=True
    )
    __table_args__ = (
       UniqueConstraint("name", "user_id", name="uq_name_item"),
    )

class ItemImage(Base):
    __tablename__ = "item_images"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    item_id: Mapped[UUID] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"))
    url: Mapped[str]
    # is_primary: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now)

    item: Mapped["Item"] = relationship(back_populates="images")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_number: Mapped[int] = mapped_column(
        Integer,
        Sequence('order_number_seq',start=1000, increment=1),
        nullable=True,
        unique=True
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    vendor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    order_type: Mapped[OrderType] = mapped_column(default=OrderType.PACKAGE)
    total_price: Mapped[Decimal] = mapped_column(default=0.00)
    amount_due_vendor: Mapped[Decimal] = mapped_column(nullable=False)
    payment_link: Mapped[str] = mapped_column(nullable=True)
    order_payment_status: Mapped[PaymentStatus] = mapped_column(
        default=PaymentStatus.PENDING
    )
    order_status: Mapped[OrderStatus] = mapped_column(nullable=True)

    require_delivery: Mapped[RequireDeliverySchema] = mapped_column(
        default=RequireDeliverySchema.PICKUP
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    owner: Mapped["User"] = relationship(
        back_populates="orders_placed", foreign_keys=[owner_id]
    )
    vendor: Mapped["User"] = relationship(
        back_populates="orders_received", foreign_keys=[vendor_id]
    )
    order_items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        lazy="selectin",
    )
    delivery: Mapped["Delivery"] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id"), primary_key=True)
    item_id: Mapped[UUID] = mapped_column(
        ForeignKey("items.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    order: Mapped["Order"] = relationship(back_populates="order_items")
    item: Mapped["Item"] = relationship(back_populates="order_items")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"))
    rider_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    dispatch_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    sender_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=True)
    image_url: Mapped[str] = mapped_column(nullable=True)
    pickup_coordinates: Mapped[Tuple[float, float]
                               ] = mapped_column(ARRAY(Float))
    dropoff_coordinates: Mapped[Tuple[float, float]
                                ] = mapped_column(ARRAY(Float))
    delivery_status: Mapped[str] = mapped_column(nullable=True)
    delivery_fee: Mapped[Decimal] = mapped_column(nullable=False)
    delivery_fee: Mapped[Decimal] = mapped_column(nullable=False)
    distance: Mapped[Decimal] = mapped_column(nullable=True)
    duration: Mapped[Decimal] = mapped_column(nullable=True)

    delivery_status: Mapped[DelivertyStatus] = mapped_column(
        default=DelivertyStatus.PENDING
    )
    delivery_type: Mapped[DeliveryType]
    amount_due_dispatch: Mapped[Decimal] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    order: Mapped["Order"] = relationship(
        back_populates="delivery",
        lazy="selectin",
    )
    rider: Mapped["User"] = relationship(
        back_populates="deliveries_as_rider", foreign_keys=[rider_id]
    )
    sender: Mapped["User"] = relationship(
        back_populates="deliveries_as_sender", foreign_keys=[sender_id]
    )


class ChargeAndCommission(Base):
    __tablename__ = "charges"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    payment_gate_way_fee: Mapped[Decimal]
    value_added_tax: Mapped[Decimal]
    payout_charge_transaction_upto_5000_naira: Mapped[Decimal]
    payout_charge_transaction_from_5001_to_50_000_naira: Mapped[Decimal]
    payout_charge_transaction_above_50_000_naira: Mapped[Decimal]
    stamp_duty: Mapped[Decimal]
    base_delivery_fee: Mapped[Decimal]
    delivery_fee_per_km: Mapped[Decimal]
    delivery_commission_percentage: Mapped[Decimal]
    food_laundry_commission_percentage: Mapped[Decimal]
    product_commission_percentage: Mapped[Decimal]
    created_at: Mapped[datetime] = mapped_column(default=datetime.today)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.today)
