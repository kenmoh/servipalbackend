from datetime import datetime
from enum import Enum
from typing import Tuple
from decimal import Decimal
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, Field

from app.schemas.item_schemas import ItemResponse
from app.schemas.status_schema import OrderStatus


class PaymentStatus(str, Enum):
    PAID = "paid"
    PENDING = "pending"
    CANCELLED = "cancelled"


class OrderType(str, Enum):
    MEAL = "meal"
    PACKAGE = "package"
    LAUNDRY = "laundry"
    PRODUCT = "product"


class OrderIssueCreate(BaseModel):
    issue_type: str = Field(..., example="WRONG_ITEM", max_length=50)
    description: str = Field(
        ..., example="Received chicken instead of fish.", max_length=500
    )


class IssueStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class OrderIssueResponse(OrderIssueCreate):
    id: UUID
    order_id: UUID
    reporter_user_id: UUID
    reported_at: datetime
    status: IssueStatus


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    IN_TRANSIT = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RECEIVED = "received"
    LAUNDRY_DELIVERES_TO_VENDORR = "delivered-to-vendor"


class DeliveryType(str, Enum):
    MEAL = "meal"
    LAUNDRY = "laundry"
    PACKAGE = "package"


class Coordinate(BaseModel):
    latitude: float
    longitude: float


class DeliveryCreate(BaseModel):
    pickup_coordinates: Tuple[float, float]
    dropoff_coordinates: Tuple[float, float]
    distance: float


# class DeliveryResponse(DeliveryCreate):
#     id: UUID
#     delivery_type: DeliveryType
#     delivery_status: DeliveryStatus
#     sender_id: UUID
#     vendor_id: UUID | None = None
#     dispatch_id: UUID | None = None
#     package_name: str | None
#     image_url: str
#     sender_phone_number: str
#     vendor_phone_number: str | None = None
#     rider_name: str | None = None
#     rider_phone_number: str | None = None
#     sender_name: str
#     distance: Decimal
#     duration: Decimal
#     delivery_fee: Decimal
#     amount_due_dispatch: Decimal
#     order: list[ItemResponse] = []
#     creared_at: datetime


class Coordinate(BaseModel):
    latitude: float
    longitude: float


class PackageCreate(BaseModel):
    name: str
    description: str
    image_urls: list[str]
    distance: Decimal
    # duration: Decimal
    pickup_coordinates: Tuple[float, float]
    dropoff_coordinates: Tuple[float, float]


class PackageResponse(BaseModel):
    id: UUID
    name: str
    description: str
    url: list[str]
    distance: Decimal
    # duration: Decimal
    pickup_coordinates: Tuple[float, float]
    dropoff_coordinates: Tuple[float, float]


class OrderItem(BaseModel):
    vendor_id: UUID
    item_id: str
    quantity: int


class OrderAndDeliverySchema(BaseModel):
    order_items: list[OrderItem]
    pickup_coordinates: Tuple[float, float]
    dropoff_coordinates: Tuple[float, float]
    distance: Decimal
    duration: Decimal
    additional_info: str | None = None


class ItemImageSchema(BaseModel):
	id: UUID
	item_id: UUID
	url: str

class OrderItemResponseSchema(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    price: Decimal
    images: list[ItemImageSchema]
    description: str
    quantity: int | None = None


class OrderResponseSchema(BaseModel):
    id: UUID
    user_id: UUID
    vendor_id: UUID
    order_type: str
    total_price: Decimal
    order_payment_status: str
    order_status: OrderStatus
    amount_due_vendor: Decimal
    payment_link: str
    order_items: list[OrderItemResponseSchema]


class WalletTransactionType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    REFUND = "refund"


class DeliveryStatusUpdateSchema(BaseModel):
    delivery_status: DeliveryStatus
