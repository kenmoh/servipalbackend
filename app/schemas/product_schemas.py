from pydantic import BaseModel, Field
from fastapi import Form
from typing import Optional, List
from uuid import UUID
from decimal import Decimal
from datetime import datetime


class ProductImage(BaseModel):
    id: UUID
    url: str
    item_id: UUID

# Base schema with common fields
class ProductBase(BaseModel):
    name: str = Form(...)
    description: str = Form(...)
    price: Decimal = Form(...)
    stock: int = Form(...)
    category_id: UUID
    sizes: Optional[str]
    colors: Optional[List[str]] = Form(None)
    in_stock: Optional[bool] = True


# Schema for creating a new product (input)
class ProductCreate(ProductBase):
    pass


# Schema for updating an existing product (input)
class ProductUpdate(ProductBase):
    pass


# Schema for representing a product in responses (output)
class ProductResponse(BaseModel):
    id: UUID
    user_id: UUID
    total_sold: int | None = None
    name: str
    description: str
    price: Decimal
    stock: int | None = None 
    category_id: UUID
    sizes: str | None = None
    colors: list[str] = []
    in_stock: bool
    images: list[ProductImage]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
