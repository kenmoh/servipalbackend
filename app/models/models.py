from re import L
from typing import Optional
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects import postgresql
import random
from uuid import UUID, uuid4
import uuid
from datetime import datetime, time
from decimal import Decimal
import logging
from typing import Optional, Tuple

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ARRAY,
    String,
    func,
    Float,
    text,
    Integer,
    UniqueConstraint,
    Index,
    Text,
    Enum,
)
from sqlalchemy.schema import Sequence
from sqlalchemy.orm import mapped_column, Mapped, relationship


from app.database.database import Base

from app.schemas.delivery_schemas import DeliveryType
from app.schemas.item_schemas import FoodGroup, ItemType, CategoryType
from app.schemas.status_schema import (
    AccountStatus,
    DeliveryStatus,
    OrderStatus,
    OrderType,
    PaymentMethod,
    PaymentStatus,
    RequireDeliverySchema,
    TransactionType,
    UserType,
)

from app.schemas.review_schema import (
    MessageType,
    ReportTag,
    ReportedUserType,
    ReviewType,
    ReportStatus,
    ReportType,
    
)


logging.basicConfig(level=logging.INFO)
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


def generate_6_digit_code():
    return str(random.randint(0, 1000000)).zfill(6)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(unique=True, nullable=False)
    password: Mapped[str]
    notification_token: Mapped[str] = mapped_column(nullable=True)
    is_blocked: Mapped[bool] = mapped_column(default=False)
    is_verified: Mapped[bool] = mapped_column(default=False)
    rider_is_suspended_for_order_cancel: Mapped[bool] = mapped_column(
        nullable=True, default=False
    )
    rider_is_suspension_until: Mapped[datetime] = mapped_column(nullable=True)
    order_cancel_count: Mapped[int] = mapped_column(nullable=True, default=0)
    reset_token: Mapped[Optional[str]] = mapped_column(nullable=True, unique=True)
    reset_token_expires: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    user_type: Mapped[UserType] = mapped_column(
        nullable=False, default=UserType.CUSTOMER
    )

    is_email_verified: Mapped[bool] = mapped_column(default=False, nullable=True)
    email_verification_code: Mapped[str] = mapped_column(nullable=True)
    email_verification_expires: Mapped[datetime] = mapped_column(nullable=True)
    account_status: Mapped[AccountStatus] = mapped_column(default=AccountStatus.PENDING)

    # Dispatcher-rider relationship with proper cascade
    dispatcher_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey(
            "users.id", ondelete="SET NULL"
        ),  # SET NULL when dispatcher is deleted
        nullable=True,
    )

    # Dispatcher-side - when dispatcher is deleted, riders become independent
    managed_riders: Mapped[list["User"]] = relationship(
        "User",
        back_populates="dispatcher",
        foreign_keys=[dispatcher_id],
        lazy="dynamic",
        cascade="all, delete-orphan",  # This ensures proper cleanup
        passive_deletes=True,  # Let database handle the cascade
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

    # Relationships with cascade
    profile: Mapped["Profile"] = relationship(
        back_populates="user",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",  # Delete profile when user is deleted
    )

    wallet: Mapped["Wallet"] = relationship(
        back_populates="user",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",  # Delete wallet when user is deleted
    )

    items: Mapped[list["Item"]] = relationship(
        back_populates="vendor",
        cascade="all, delete-orphan",  # Delete items when vendor is deleted
    )

    orders_placed: Mapped[list["Order"]] = relationship(
        back_populates="owner",
        foreign_keys="Order.owner_id",
    )

    orders_received: Mapped[list["Order"]] = relationship(
        back_populates="vendor",
        foreign_keys="Order.vendor_id",
    )

    deliveries_as_rider: Mapped[list["Delivery"]] = relationship(
        back_populates="rider",
        foreign_keys="Delivery.rider_id",
    )

    deliveries_as_sender: Mapped[list["Delivery"]] = relationship(
        back_populates="sender",
        foreign_keys="Delivery.sender_id",
    )

    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )

    reviews_written: Mapped[list["Review"]] = relationship(
        back_populates="reviewer",
        cascade="all, delete-orphan",
        foreign_keys="[Review.reviewer_id]",
    )

    reviews_received: Mapped[list["Review"]] = relationship(
        back_populates="reviewee",
        cascade="all, delete-orphan",
        foreign_keys="[Review.reviewee_id]",
    )

    sent_messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="sender",
        foreign_keys="Message.sender_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    reports_received: Mapped[list["UserReport"]] = relationship(
        "UserReport",
        back_populates="defendant",
        foreign_keys="UserReport.defendant_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    issues_reported: Mapped[list["UserReport"]] = relationship(
        "UserReport",
        back_populates="complainant",
        foreign_keys="UserReport.complainant_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )



class Profile(Base):
    __tablename__ = "profile"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, primary_key=True
    )
    business_name: Mapped[str] = mapped_column(nullable=True)
    bank_name: Mapped[str] = mapped_column(nullable=True)
    bank_account_number: Mapped[str] = mapped_column(nullable=True)
    business_address: Mapped[str] = mapped_column(nullable=True)
    business_registration_number: Mapped[str] = mapped_column(nullable=True)
    account_holder_name: Mapped[str] = mapped_column(nullable=True)
    opening_hours: Mapped[time] = mapped_column(nullable=True)
    closing_hours: Mapped[time] = mapped_column(nullable=True)
    full_name: Mapped[str] = mapped_column(nullable=True)
    phone_number: Mapped[str] = mapped_column(unique=True, nullable=False)
    store_name: Mapped[str] = mapped_column(unique=True, nullable=True)
    bike_number: Mapped[str] = mapped_column(unique=True, nullable=True)
    is_phone_verified: Mapped[bool] = mapped_column(default=False, nullable=True)
    phone_verification_code: Mapped[str] = mapped_column(nullable=True)
    phone_verification_expires: Mapped[datetime] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    user: Mapped["User"] = relationship(back_populates="profile")
    profile_image: Mapped["ProfileImage"] = relationship(
        back_populates="profile",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ProfileImage(Base):
    __tablename__ = "profile_images"

    profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("profile.user_id", ondelete="CASCADE"), primary_key=True, unique=True
    )
    profile_image_url: Mapped[str] = mapped_column(nullable=True)
    backdrop_image_url: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    profile: Mapped["Profile"] = relationship(back_populates="profile_image")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    terminated_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    device_info: Mapped[str]
    ip_address: Mapped[str]
    last_active: Mapped[datetime] = mapped_column(default=datetime.now)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    termination_reason: Mapped[str] = mapped_column(nullable=True)
    terminated_by_user: Mapped["User"] = relationship(
        "User", foreign_keys=[terminated_by], backref="terminated_sessions"
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
    payment_by: Mapped[str] = mapped_column(nullable=True)
    transaction_type: Mapped[TransactionType]
    payment_status: Mapped[PaymentStatus] = mapped_column(default=PaymentStatus.PENDING)
    payment_method: Mapped[PaymentMethod] = mapped_column(nullable=True)
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
    account_status: Mapped[AccountStatus] = mapped_column(
        default=AccountStatus.PENDING, nullable=True
    )  # TODO: remove nullable
    user_type: Mapped[str] = mapped_column(nullable=True)  # TODO: change to False
    email: Mapped[str] = mapped_column(nullable=True)  # TODO: change to False
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
    category_type: Mapped[CategoryType] = mapped_column(
        nullable=True, default=CategoryType.FOOD
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    items: Mapped[list["Item"]] = relationship(back_populates="category")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    item_type: Mapped[ItemType]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str]
    store_name: Mapped[str] = mapped_column(nullable=True)
    description: Mapped[str] = mapped_column(nullable=True)
    price: Mapped[Decimal] = mapped_column(default=0.00, nullable=False)
    sizes: Mapped[str] = mapped_column(nullable=True)
    side: Mapped[str] = mapped_column(nullable=True)
    colors: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    stock: Mapped[int] = mapped_column(nullable=True)
    in_stock: Mapped[bool] = mapped_column(default=True)
    total_sold: Mapped[int] = mapped_column(nullable=True)

    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )
    food_group: Mapped[FoodGroup] = mapped_column(
        Enum(FoodGroup, name="foodgroup", create_constraint=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    vendor: Mapped["User"] = relationship(back_populates="items")
    category: Mapped["Category"] = relationship(back_populates="items")
    order_items: Mapped[list["OrderItem"]] = relationship(back_populates="item")
    images: Mapped[list["ItemImage"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=True,
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    # __table_args__ = (UniqueConstraint(
    #     "name", "user_id", name="uq_name_item"),)

    __table_args__ = (
        Index(
            "uq_name_user_non_package",
            "name",
            "user_id",
            unique=True,
            postgresql_where=text("item_type != 'PACKAGE'"),
        ),
    )


class ItemImage(Base):
    __tablename__ = "item_images"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    item_id: Mapped[UUID] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    url: Mapped[str]
    # is_primary: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    item: Mapped["Item"] = relationship(back_populates="images")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_number: Mapped[int] = mapped_column(
        Integer,
        Sequence("order_number_seq", start=1000, increment=1),
        nullable=True,
        unique=True,
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    vendor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    order_type: Mapped[OrderType] = mapped_column(default=OrderType.PACKAGE)
    total_price: Mapped[Decimal] = mapped_column(default=0.00)
    amount_due_vendor: Mapped[Decimal] = mapped_column(nullable=False)
    payment_link: Mapped[str] = mapped_column(nullable=True)
    additional_info: Mapped[str] = mapped_column(nullable=True)
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
    user_reviews: Mapped[list["Review"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    issues: Mapped[list["UserReport"]] = relationship(
        "UserReport",
        back_populates="order",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    item_id: Mapped[UUID] = mapped_column(ForeignKey("items.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(default=1)
    sizes: Mapped[str] = mapped_column(ARRAY(String), nullable=True)
    colors: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    order: Mapped["Order"] = relationship(back_populates="order_items")
    item: Mapped["Item"] = relationship(back_populates="order_items")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"))
    rider_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    dispatch_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    vendor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sender_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sender_phone_number: Mapped[str]
    rider_phone_number: Mapped[str] = mapped_column(nullable=True)
    image_url: Mapped[str] = mapped_column(nullable=True)
    pickup_coordinates: Mapped[Tuple[float, float]] = mapped_column(ARRAY(Float))
    dropoff_coordinates: Mapped[Tuple[float, float]] = mapped_column(ARRAY(Float))
    delivery_fee: Mapped[Decimal] = mapped_column(nullable=False)
    distance: Mapped[Decimal] = mapped_column(nullable=True)
    duration: Mapped[str] = mapped_column(nullable=True)
    origin: Mapped[str]
    destination: Mapped[str]

    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        default=DeliveryStatus.PENDING
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
    issues: Mapped[list["UserReport"]] = relationship(
        "UserReport",
        back_populates="delivery",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class ChargeAndCommission(Base):
    __tablename__ = "charges"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    payment_gate_way_fee: Mapped[Decimal]  # 0.014
    value_added_tax: Mapped[Decimal]  # 0.075
    payout_charge_transaction_upto_5000_naira: Mapped[Decimal]  # 10
    payout_charge_transaction_from_5001_to_50_000_naira: Mapped[Decimal]  # 25
    payout_charge_transaction_above_50_000_naira: Mapped[Decimal]  # 50
    stamp_duty: Mapped[Decimal]  # 50
    base_delivery_fee: Mapped[Decimal]  # 1750
    delivery_fee_per_km: Mapped[Decimal]  # 150
    delivery_commission_percentage: Mapped[Decimal]  # 0.15
    food_laundry_commission_percentage: Mapped[Decimal]  # 0.10
    product_commission_percentage: Mapped[Decimal]  # 0.10
    created_at: Mapped[datetime] = mapped_column(default=datetime.today)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.today)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    reviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id")
    )  # Who wrote the review
    order_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("orders.id"), nullable=True
    )
    item_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("items.id"), nullable=True
    )

    reviewee_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )  # Who is being reviewed

    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    review_type: Mapped[ReviewType] = mapped_column(
        nullable=False
    )  # Enum: ITEM, RIDER, VENDOR
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    # RELATIONSHIPS
    reviewer: Mapped["User"] = relationship(
        "User", back_populates="reviews_written", foreign_keys=[reviewer_id]
    )

    reviewee: Mapped["User"] = relationship(
        "User", back_populates="reviews_received", foreign_keys=[reviewee_id]
    )

    order: Mapped[Optional["Order"]] = relationship(back_populates="user_reviews")
    item: Mapped[Optional["Item"]] = relationship(back_populates="reviews")

    __table_args__ = (
        # Optional: Prevent duplicate reviews (e.g., per reviewer/order/item)
        UniqueConstraint("reviewer_id", "order_id", name="uq_user_order_review"),
        UniqueConstraint("reviewer_id", "item_id", name="uq_user_item_review"),
    )



# reportedusertype reporttag reportstatus messagetype reporttype
class UserReport(Base):
    """Report model for handling user reports/issues"""

    __tablename__ = "user_reports"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Foreign keys to either Order or Delivery
    order_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("orders.id"), nullable=True
    )
    # REMOVE
    delivery_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("deliveries.id"), nullable=True
    )

    complainant_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    defendant_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    reported_user_type: Mapped[ReportedUserType]
    report_tag: Mapped[ReportTag]
    report_type: Mapped[ReportType] = mapped_column(default=ReportType.OTHERS)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    report_status: Mapped[ReportStatus] = mapped_column(default=ReportStatus.PENDING)
    is_read: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    # Relationships
    defendant: Mapped["User"] = relationship(
        "User", foreign_keys=[defendant_id], back_populates="reports_received"
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    read_status: Mapped[list["UserReportReadStatus"]] = relationship(
        "UserReportReadStatus",
        back_populates="report",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    complainant: Mapped["User"] = relationship(
        "User",
        foreign_keys=[complainant_id],
        back_populates="issues_reported",
        lazy="joined",
    )

    order: Mapped[Optional["Order"]] = relationship(
        "Order", back_populates="issues", lazy="selectin"
    )

    delivery: Mapped[Optional["Delivery"]] = relationship(
        "Delivery", back_populates="issues", lazy="selectin"
    )
    __table_args__ = (
        # One report per reporter per order (if vendor is involved)
        UniqueConstraint(
            "complainant_id",
            "order_id",
            "defendant_id",
            name="uq_reporter_order_report",
        ),
        # One report per reporter per delivery (if dispatch is involved)
        UniqueConstraint(
            "complainant_id",
            "delivery_id", # REMOVE
            "defendant_id",
            name="uq_reporter_delivery_report",
        ),
    )


class Message(Base):
    """Message model for both broadcast messages and report thread messages"""

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    message_type: Mapped[MessageType] = mapped_column(default=MessageType.REPORT)
    content: Mapped[str] = mapped_column(Text)
    sender_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=True)

    # For report messages
    report_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("user_reports.id"), nullable=True
    )
    role: Mapped[Optional[UserType]] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.now, onupdate=datetime.now
    )

    # Relationships
    sender: Mapped["User"] = relationship(
        "User", foreign_keys=[sender_id], back_populates="sent_messages"
    )
   
   
    report: Mapped[Optional["UserReport"]] = relationship(
        "UserReport", back_populates="messages"
    )
    read_status: Mapped[list["MessageReadStatus"]] = relationship(
        "MessageReadStatus", back_populates="message", cascade="all, delete-orphan"
    )


class MessageReadStatus(Base):
    """Track read status of messages per user"""

    __tablename__ = "message_read_status"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    read: Mapped[bool] = mapped_column(default=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(default=datetime.now, onupdate=datetime.now)

    # Relationships
    message: Mapped["Message"] = relationship("Message", back_populates="read_status")
    user: Mapped["User"] = relationship("User")


class UserReportReadStatus(Base):
    """Track read status of reports (threads) per user for badge and notification purposes."""
    __tablename__ = "user_report_read_status"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("user_reports.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_read: Mapped[bool] = mapped_column(default=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(default=datetime.now, onupdate=datetime.now)

    # Relationships
    report: Mapped["UserReport"] = relationship("UserReport", back_populates="read_status")
    user: Mapped["User"] = relationship("User")
