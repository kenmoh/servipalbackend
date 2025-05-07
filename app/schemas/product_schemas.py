from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from decimal import Decimal
from datetime import datetime


# Base schema with common fields
class ProductBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=10)
    price: Decimal = Field(..., gt=0, decimal_places=2)
    stock: int = Field(..., ge=0)
    category_id: UUID
    image_urls: List[str] = Field(default_factory=list)
    sizes: Optional[str] = None
    colors: Optional[List[str]] = None
    in_stock: Optional[bool] = True  # Can be derived or set


# Schema for creating a new product (input)
class ProductCreate(ProductBase):
    pass


# Schema for updating an existing product (input)
# All fields are optional for partial updates
class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, min_length=10)
    price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    stock: Optional[int] = Field(None, ge=0)
    category_id: Optional[UUID] = None
    image_urls: Optional[List[str]] = None
    sizes: Optional[str] = None
    colors: Optional[List[str]] = None
    in_stock: Optional[bool] = None


# Schema for representing a product in responses (output)
class ProductResponse(ProductBase):
    id: UUID
    seller_id: UUID
    total_sold: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
