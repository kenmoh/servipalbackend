from uuid import UUID
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel


class FoodGroup(str, Enum):
    APPETIZER = "appetizer"
    MAIN_COURSE = "main_course"
    DESSERT = "dessert"
    OTHERS='others'


class CategoryType(str, Enum):
    FOOD = "food"
    PRODUCT = "product"


class CategoryCreate(BaseModel):
    name: str


class CategoryResponse(CategoryCreate):
    id: UUID
    category_type: CategoryType


class ItemType(str, Enum):
    FOOD = "food"
    PACKAGE = "package"
    LAUNDRY = "laundry"
    PRODUCT = "product"


class ItemCreate(BaseModel):
    name: str
    description: str | None = None
    price: Decimal
    item_type: ItemType
    category_id: UUID | None = None
    colors: list[str] = []
    sizes: str | None = None
    stock: int | None = None


class ItemCreateResponse(BaseModel):
    name: str
    description: str | None = None
    price: Decimal
    item_type: ItemType
    colors: list[str] = []
    stock: int | None = None
    sizes: str | None = None


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

    class Config:
        from_attributes = True


class MenuSchema(BaseModel):
    id: UUID
    name: str
    description: str
    price: Decimal
    image_url: str
 


class MenuResponseSchema(BaseModel):
    vendor_id: UUID
    menu_item: list[MenuSchema]
    # total_items: int

class MenuBase(BaseModel):
    id: UUID
    name: str
    item_type: ItemType
    price: Decimal
    images: list[ItemImageSchema]



class RestaurantMenuResponseSchema(MenuBase):
    restaurant_id: UUID
    description: str
   


class LaundryMenuResponseSchema(MenuBase):
    laundry_id: str