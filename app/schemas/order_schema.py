from datetime import datetime
from enum import Enum
from decimal import Decimal
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, Field

from app.schemas.status_schema import OrderStatus
from app.schemas.status_schema import RequireDeliverySchema


class PaymentStatus(str, Enum):
    PAID = "paid"
    PENDING = "pending"
    CANCELLED = "cancelled"


class OrderType(str, Enum):
    FOOD = "food"
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


class BankTransferResponse(BaseModel):
    status: str
    message: str
    transfer_reference: str
    transfer_account: str
    transfer_bank: str
    account_expiration: str
    transfer_note: str
    transfer_amount: str
    mode: str


class PackageCreate(BaseModel):
    name: str
    description: str
    distance: Decimal
    origin: str
    destination: str
    duration: str
    pickup_coordinates: list[float]
    dropoff_coordinates: list[float]


class PackageResponse(BaseModel):
    id: UUID
    name: str
    description: str
    url: list[str]
    distance: Decimal
    duration: str
    pickup_coordinates: list[float]
    dropoff_coordinates: list[float]


class OrderItemCreate(BaseModel):
    vendor_id: UUID
    item_id: str
    quantity: int


class OrderAndDeliverySchema(BaseModel):
    order_items: list[OrderItemCreate]
    pickup_coordinates: list[float]
    dropoff_coordinates: list[float]
    distance: Decimal | None = None
    require_delivery: RequireDeliverySchema
    is_one_way_delivery: bool = True
    duration: str | None = None
    origin: str | None = None
    destination: str | None = None
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
    grand_total: Decimal | None = None
    order_payment_status: str
    business_name: str | None = None
    require_delivery: str
    order_status: OrderStatus | None = None
    order_number: int | None = None
    amount_due_vendor: Decimal
    vendor_pickup_dropoff_charge: Decimal | None = None
    payment_link: str
    created_at: datetime
    is_one_way_delivery: bool
    order_items: list[OrderItemResponseSchema]
    cancel_reason: str | None = None


class DeliveryStatusUpdateSchema(BaseModel):
    delivery_status: str | None = None
    order_status: str | None = None


class OrderStatusResponseSchema(BaseModel):
    order_status: str
