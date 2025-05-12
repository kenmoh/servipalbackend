from pydantic import BaseModel, Field
from fastapi import Form
from typing import Optional, List
from uuid import UUID
from decimal import Decimal
from datetime import datetime


# Base schema with common fields
class ProductBase(BaseModel):
    name: str = Form(...)
    description: str = Form(...)
    price: Decimal = Form(...)
    stock: int = Form(...)
    category_id: UUID
    sizes: Optional[str] = Form(None)
    colors: Optional[List[str]] = Form(None)
    in_stock: Optional[bool] = True


# Schema for creating a new product (input)
class ProductCreate(ProductBase):
    pass


# Schema for updating an existing product (input)
class ProductUpdate(ProductBase):
    pass


# Schema for representing a product in responses (output)
class ProductResponse(ProductBase):
    id: UUID
    seller_id: UUID = Field(alias='user_id')
    total_sold: int  = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
