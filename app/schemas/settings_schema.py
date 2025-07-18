from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal
from datetime import datetime


class ChargeAndCommissionSchema(BaseModel):
    """Schema for viewing charge and commission settings"""

    id: UUID
    payment_gate_way_fee: Decimal
    value_added_tax: Decimal
    payout_charge_transaction_upto_5000_naira: Decimal
    payout_charge_transaction_from_5001_to_50_000_naira: Decimal
    payout_charge_transaction_above_50_000_naira: Decimal
    stamp_duty: Decimal
    base_delivery_fee: Decimal
    delivery_fee_per_km: Decimal
    delivery_commission_percentage: Decimal
    food_laundry_commission_percentage: Decimal
    product_commission_percentage: Decimal
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChargeAndCommissionUpdateSchema(BaseModel):
    """Schema for updating charge and commission settings"""

    payment_gate_way_fee: Optional[Decimal] = Field(
        None, ge=0, description="Payment gateway fee (e.g., 0.014 for 1.4%)"
    )
    value_added_tax: Optional[Decimal] = Field(
        None, ge=0, le=1, description="VAT percentage (e.g., 0.075 for 7.5%)"
    )
    payout_charge_transaction_upto_5000_naira: Optional[Decimal] = Field(
        None, ge=0, description="Charge for transactions up to ₦5,000"
    )
    payout_charge_transaction_from_5001_to_50_000_naira: Optional[Decimal] = Field(
        None, ge=0, description="Charge for transactions from ₦5,001 to ₦50,000"
    )
    payout_charge_transaction_above_50_000_naira: Optional[Decimal] = Field(
        None, ge=0, description="Charge for transactions above ₦50,000"
    )
    stamp_duty: Optional[Decimal] = Field(None, ge=0, description="Stamp duty charge")
    base_delivery_fee: Optional[Decimal] = Field(
        None, ge=0, description="Base delivery fee"
    )
    delivery_fee_per_km: Optional[Decimal] = Field(
        None, ge=0, description="Delivery fee per kilometer"
    )
    delivery_commission_percentage: Optional[Decimal] = Field(
        None,
        ge=0,
        le=1,
        description="Delivery commission percentage (e.g., 0.15 for 15%)",
    )
    food_laundry_commission_percentage: Optional[Decimal] = Field(
        None,
        ge=0,
        le=1,
        description="Food/laundry commission percentage (e.g., 0.10 for 10%)",
    )
    product_commission_percentage: Optional[Decimal] = Field(
        None,
        ge=0,
        le=1,
        description="Product commission percentage (e.g., 0.10 for 10%)",
    )

    class Config:
        from_attributes = True


class ChargeAndCommissionCreateSchema(BaseModel):
    """Schema for creating initial charge and commission settings"""

    payment_gate_way_fee: Decimal = Field(
        ..., ge=0, description="Payment gateway fee (e.g., 0.014 for 1.4%)"
    )
    value_added_tax: Decimal = Field(
        ..., ge=0, le=1, description="VAT percentage (e.g., 0.075 for 7.5%)"
    )
    payout_charge_transaction_upto_5000_naira: Decimal = Field(
        ..., ge=0, description="Charge for transactions up to ₦5,000"
    )
    payout_charge_transaction_from_5001_to_50_000_naira: Decimal = Field(
        ..., ge=0, description="Charge for transactions from ₦5,001 to ₦50,000"
    )
    payout_charge_transaction_above_50_000_naira: Decimal = Field(
        ..., ge=0, description="Charge for transactions above ₦50,000"
    )
    stamp_duty: Decimal = Field(..., ge=0, description="Stamp duty charge")
    base_delivery_fee: Decimal = Field(..., ge=0, description="Base delivery fee")
    delivery_fee_per_km: Decimal = Field(
        ..., ge=0, description="Delivery fee per kilometer"
    )
    delivery_commission_percentage: Decimal = Field(
        ...,
        ge=0,
        le=1,
        description="Delivery commission percentage (e.g., 0.15 for 15%)",
    )
    food_laundry_commission_percentage: Decimal = Field(
        ...,
        ge=0,
        le=1,
        description="Food/laundry commission percentage (e.g., 0.10 for 10%)",
    )
    product_commission_percentage: Decimal = Field(
        ...,
        ge=0,
        le=1,
        description="Product commission percentage (e.g., 0.10 for 10%)",
    )

    class Config:
        from_attributes = True


class SettingsResponseSchema(BaseModel):
    """Response schema for settings operations"""

    success: bool
    message: str
    data: Optional[ChargeAndCommissionSchema] = None

    class Config:
        from_attributes = True
