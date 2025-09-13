from pydantic import BaseModel, field_serializer
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
    category_id: UUID = Form(...)
    sizes: Optional[str] = Form(None)
    colors: Optional[List[str]] = Form(None)


# Schema for creating a new product (input)
class ProductCreate(BaseModel):
    name: str
    description: str
    price: Decimal
    stock: int
    category_id: UUID
    sizes: str | None = None
    colors: Optional[List[str]] = []


# Schema for updating an existing product (input)
class ProductUpdate(ProductCreate):
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
    store_name: str | None = None
    colors: list[str] = []
    in_stock: bool
    images: list[ProductImage]
    created_at: datetime
    updated_at: datetime

    @field_serializer('price')
    def serialize_price(self, price: Decimal) -> float:
        """Convert to float to avoid scientific notation in JSON."""
        return float(f"{price:.2f}")

    class Config:
        from_attributes = True
