from decimal import Decimal
from enum import Enum
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


class ItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: Decimal
    item_type: ItemType
    image_url: str
    category_id: UUID | None = None


class ItemResponse(ItemCreate):
    id: UUID
    user_id: UUID
