from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.schemas.status_schema import PaymentStatus, TransactionType

# Schema for the request body when buying a product


# Enum for payment methods
class PaymentMethod(str, Enum):
    WALLET = "wallet"
    CARD = "card"


class ProductBuyRequest(BaseModel):
    quantity: int = Field(1, gt=0, description="Number of units to purchase")
    colors: list[str] = []
    sizes: list[str] = []
    additional_info: str | None = None


class TopUpRequestSchema(BaseModel):
    amount: Decimal = Field(ge=1000, le=100000, description="Amount to top up")


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
