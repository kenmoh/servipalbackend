from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from typing import Optional
from app.schemas.status_schema import TransactionType, PaymentStatus, PaymentMethod


class TransactionSchema(BaseModel):
    id: UUID
    wallet_id: UUID
    amount: Decimal
    payment_by: Optional[str] = None
    transaction_type: TransactionType
    payment_status: PaymentStatus
    payment_method: Optional[PaymentMethod] = None
    payment_link: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
        from_attributes = True


class TransactionCreateSchema(BaseModel):
    """Schema for creating a new transaction"""

    wallet_id: UUID
    amount: Decimal
    payment_by: Optional[str] = None
    transaction_type: TransactionType
    payment_status: PaymentStatus = PaymentStatus.PENDING
    payment_method: Optional[PaymentMethod] = None
    payment_link: Optional[str] = None

    class Config:
        orm_mode = True
        from_attributes = True


class TransactionUpdateSchema(BaseModel):
    """Schema for updating an existing transaction"""

    payment_status: Optional[PaymentStatus] = None
    payment_method: Optional[PaymentMethod] = None
    payment_link: Optional[str] = None

    class Config:
        orm_mode = True
        from_attributes = True
