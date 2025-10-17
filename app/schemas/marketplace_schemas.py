from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
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
    id: UUID
    item_id: UUID
    url: str

    class Config:
        from_attributes = True


class ProductOrderItemResponse(BaseModel):
    item_id: UUID
    user_id: UUID  # This is the vendor's user_id from the item
    name: str
    price: Decimal
    images: list[ItemImageResponse]
    description: str
    quantity: int

    class Config:
        from_attributes = True


class ProductOrderResponse(BaseModel):
    id: UUID
    user_id: UUID  # owner_id
    vendor_id: UUID
    order_type: OrderType
    total_price: Decimal
    order_payment_status: PaymentStatus
    order_status: OrderStatus
    order_number: int
    additional_info: str | None = None
    amount_due_vendor: Decimal
    payment_link: str
    # created_at: datetime
    order_items: list[ProductOrderItemResponse]

    class Config:
        from_attributes = True


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
