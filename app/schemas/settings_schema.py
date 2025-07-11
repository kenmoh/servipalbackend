from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal
from datetime import datetime


class ChargeAndCommissionSchema(BaseModel):
    """Schema for viewing charge and commission settings"""
    id: int
    payout_charge_transaction_upto_5000_naira: Decimal
    payout_charge_transaction_from_5001_to_50_000_naira: Decimal
    payout_charge_transaction_above_50_000_naira: Decimal
    value_added_tax: Decimal
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
        from_attributes = True


class ChargeAndCommissionUpdateSchema(BaseModel):
    """Schema for updating charge and commission settings"""
    payout_charge_transaction_upto_5000_naira: Optional[Decimal] = Field(
        None, 
        ge=0, 
        description="Charge for transactions up to ₦5,000"
    )
    payout_charge_transaction_from_5001_to_50_000_naira: Optional[Decimal] = Field(
        None, 
        ge=0, 
        description="Charge for transactions from ₦5,001 to ₦50,000"
    )
    payout_charge_transaction_above_50_000_naira: Optional[Decimal] = Field(
        None, 
        ge=0, 
        description="Charge for transactions above ₦50,000"
    )
    value_added_tax: Optional[Decimal] = Field(
        None, 
        ge=0, 
        le=1, 
        description="VAT percentage (e.g., 0.075 for 7.5%)"
    )

    class Config:
        orm_mode = True
        from_attributes = True


class ChargeAndCommissionCreateSchema(BaseModel):
    """Schema for creating initial charge and commission settings"""
    payout_charge_transaction_upto_5000_naira: Decimal = Field(
        ..., 
        ge=0, 
        description="Charge for transactions up to ₦5,000"
    )
    payout_charge_transaction_from_5001_to_50_000_naira: Decimal = Field(
        ..., 
        ge=0, 
        description="Charge for transactions from ₦5,001 to ₦50,000"
    )
    payout_charge_transaction_above_50_000_naira: Decimal = Field(
        ..., 
        ge=0, 
        description="Charge for transactions above ₦50,000"
    )
    value_added_tax: Decimal = Field(
        ..., 
        ge=0, 
        le=1, 
        description="VAT percentage (e.g., 0.075 for 7.5%)"
    )

    class Config:
        orm_mode = True
        from_attributes = True


class SettingsResponseSchema(BaseModel):
    """Response schema for settings operations"""
    success: bool
    message: str
    data: Optional[ChargeAndCommissionSchema] = None

    class Config:
        orm_mode = True
        from_attributes = True
