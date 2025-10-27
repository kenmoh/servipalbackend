from enum import Enum
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from uuid import UUID
from datetime import datetime, timedelta
from decimal import Decimal

from app.schemas.order_schema import OrderType
from app.schemas.status_schema import OrderStatus, PaymentStatus, TransactionType

# Schema for the request body when buying a product


# Enum for payment methods
class PaymentMethod(str, Enum):
    WALLET = "wallet"
    CARD = "card"


class ProductBuyRequest(BaseModel):
    quantity: int = Field(1, gt=0, description="Number of units to purchase")

    sizes: str | None = None
    colors: list[str] = []
    additional_info: str


class ItemImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    item_id: UUID
    url: str
    is_primary: Optional[bool] = None


class Item(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    name: str
    description: str
    price: float
    sizes: str | None = None
    colors: List[str] = []
    images: List[ItemImageResponse] = []


class OrderItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    order_id: UUID
    quantity: int
    sizes: List = []
    colors: List = []
    created_at: datetime
    item: Item


class ProductOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    vendor_id: UUID
    order_number: int
    order_status: str
    order_payment_status: str
    total_price: float
    grand_total: float
    amount_due_vendor: float
    payment_link: str
    additional_info: str
    order_items: List[OrderItem]
    created_at: datetime
    updated_at: datetime


class TopUpRequestSchema(BaseModel):
    amount: Decimal = Field(ge=1000, le=100000, description="Amount to top up")


class TopUpResponseSchema(TopUpRequestSchema):
    payment_link: str


class TransactionResponse(BaseModel):
    id: UUID
    wallet_id: UUID
    product_id: UUID | None = None
    amount: Decimal
    transaction_type: TransactionType
    payment_status: PaymentStatus
    payment_link: str | None = None
    created_at: datetime
    updated_at: datetime


class BankCode(BaseModel):
    bank_code: str


class TransferDetailResponseSchema(BaseModel):
    status: str
    message: str
    transfer_reference: str
    transfer_account: str
    transfer_bank: str
    account_expiration: str
    transfer_note: str
    transfer_amount: str
    mode: str


class WithdrawalShema(BaseModel):
    status: str
    message: str
    transaction_id: UUID
    amount: Decimal
    bank_name: str
    account_number: str
    beneficiary: str
    timestamp: datetime
