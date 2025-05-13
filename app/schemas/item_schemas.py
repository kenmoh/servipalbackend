from datetime import datetime
from decimal import Decimal
from enum import Enum
from fastapi import Form
from pydantic import BaseModel
from typing import Optional
from uuid import UUID


class CategoryCreate(BaseModel):
    name: str


class CategoryResponse(BaseModel):
    id: UUID
    name: str


class ItemType(str, Enum):
    FOOD = "food"
    PACKAGE = "package"
    LAUNDRY = "laundry"
    PRODUCT = "product"


class ItemCreate(BaseModel):
    name: str = (Form(...),)
    description: str = (Form(...),)
    price: Decimal = (Form(...),)
    item_type: ItemType = (Form(...),)
    category_id: UUID = (Form(...),)


class ItemImageSchema(BaseModel):
    id: UUID
    item_id: UUID
    url: str


class ReviewResponseSchema(BaseModel):
    item_id: UUID
    rating: int
    comment: str | None = None
    created_at: datetime


class ItemResponse(ItemCreate):
    id: UUID
    user_id: UUID
    images: list[ItemImageSchema]
    reviews: list[ReviewResponseSchema]
