from uuid import UUID
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel


class FoodGroup(str, Enum):
    """Food Group Enum"""

    APPETIZER = "appetizer"
    MAIN_COURSE = "main_course"
    DESSERT = "dessert"
    OTHERS = "others"


class CategoryType(str, Enum):
    """Category Type Enum"""

    FOOD = "food"
    PRODUCT = "product"


class CategoryCreate(BaseModel):
    name: str


class CategoryResponse(CategoryCreate):
    """Category Response"""

    id: UUID
    category_type: CategoryType


class ItemType(str, Enum):
    FOOD = "food"
    PACKAGE = "package"
    LAUNDRY = "laundry"
    PRODUCT = "product"


class ItemImageSchema(BaseModel):
    id: UUID
    item_id: UUID
    url: str


class MenuItemCreate(BaseModel):
    name: str
    description: str
    price: Decimal
    side: str | None = None
    category_id: UUID 
    food_group: FoodGroup 


class LaundryItemCreate(BaseModel):
    name: str
    description: str | None = None
    price: Decimal


class ItemCreate(BaseModel):
    name: str
    description: str | None = None
    price: Decimal
    item_type: ItemType
    category_id: UUID | None = None
    side: str | None = None
    colors: list[str] = []
    sizes: str | None = None
    stock: int | None = None


class ItemResponse(ItemCreate):
    id: UUID
    user_id: UUID
    images: list[ItemImageSchema]

    class Config:
        from_attributes = True


# class ReviewResponseSchema(BaseModel):
#     item_id: UUID
#     rating: int
#     comment: str
#     created_at: datetime


# class ItemResponse(ItemCreate):
#     id: UUID
#     user_id: UUID
#     images: list[ItemImageSchema]
#     reviews: list[ReviewResponseSchema] = []

#     class Config:
#         from_attributes = True


# class MenuResponseSchema(BaseModel):
#     id: UUID
#     vendor_id: UUID
#     name: str
#     description: str
#     price: Decimal
#     item_type: ItemType
#     group: FoodGroup
#     image_url: list[ItemImageSchema]


class MenuBase(BaseModel):
    id: UUID
    name: str
    item_type: ItemType
    price: Decimal
    is_deleted: bool
    images: list[ItemImageSchema]


class MenuResponseSchema(MenuBase):
    user_id: UUID
    category_id: UUID
    side: str | None = None
    food_group: FoodGroup
    description: str
    is_deleted: bool = False

class LaundryMenuResponseSchema(MenuBase):
    pass
