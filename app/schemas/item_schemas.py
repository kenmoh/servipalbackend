from datetime import datetime
from decimal import Decimal
from enum import Enum
from fastapi import Form
from pydantic import BaseModel, Field
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




class ItemCreateResponse(BaseModel):
    name: str 
    description: str
    price: Decimal
    item_type: ItemType
    # category_id: UUID



class ItemImageSchema(BaseModel):
    id: UUID
    item_id: UUID
    url: str


class ReviewResponseSchema(BaseModel):
    item_id: UUID 
    rating: int 
    comment: str 
    created_at: datetime 



class ItemResponse(ItemCreateResponse):
    id: UUID
    user_id: UUID
    images: list[ItemImageSchema]
    reviews: list[ReviewResponseSchema] = []

class MenuWithReviewSchema(BaseModel):

    id: UUID
    name: str
    description: str
    price: Decimal
    image_url: str
    average_rating: float
    review_count: int
    reviews: list[ReviewResponseSchema] = []

class MenuWithReviewResponseSchema(BaseModel):        
    vendor_id: UUID
    menu_item: list[MenuWithReviewSchema]
    total_items: int