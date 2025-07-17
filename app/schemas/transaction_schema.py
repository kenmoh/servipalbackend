from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
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
    from_user: str | None = None
    to_user: str | None = None
    transaction_direction: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        
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
        
        from_attributes = True


class TransactionUpdateSchema(BaseModel):
    """Schema for updating an existing transaction"""

    payment_status: Optional[PaymentStatus] = None
    payment_method: Optional[PaymentMethod] = None
    payment_link: Optional[str] = None

    class Config:
        
        from_attributes = True


class TransactionFilterSchema(BaseModel):
    """Schema for filtering transactions"""

    transaction_type: Optional[TransactionType] = None
    payment_status: Optional[PaymentStatus] = None
    payment_method: Optional[PaymentMethod] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None


class TransactionResponseSchema(BaseModel):
    """Schema for transaction response with pagination"""

    transactions: List[TransactionSchema]
    total_count: int
    page: int
    page_size: int
    total_pages: int

    class Config:
        
        from_attributes = True
