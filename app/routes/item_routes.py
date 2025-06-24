from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Depends, File, UploadFile, status, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_db, get_current_user
from app.models.models import User
from app.schemas.item_schemas import (
    CategoryCreate,
    CategoryResponse,
    LaundryMenuResponseSchema,
    MenuItemCreate,
    RestaurantMenuResponseSchema,
    ItemType,
)
from app.services import item_service

router = APIRouter(prefix="/api/items", tags=["Items"])


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new item category",
    description="Allows VENDOR users to create a new category for items.",
)
async def create_new_category(
    category_data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryResponse:
    """
    Endpoint to create a new item category.
    - Requires authenticated VENDOR user.
    """
    return await item_service.create_category(db, current_user, category_data)


@router.post(
    "/menu-item-create",
    status_code=status.HTTP_201_CREATED,
)
async def create_menu_item(
    name: str = Form(...),
    description: str = Form(None),
    price: Decimal = Form(...),
    side: str = Form(None),
    category_id: UUID | None = Form(None),
    group: UUID | None = Form(None),
    item_type: ItemType = Form(...),
    images: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RestaurantMenuResponseSchema | LaundryMenuResponseSchema:
    """
    Endpoint to create a new food item.
    - Requires authenticated VENDOR user.
    - The specified category_id must exist.
    """

    item_data = MenuItemCreate(
        name=name,
        description=description,
        price=price,
        images=images,
        item_type=item_type,
        category_id=category_id,
        food_group=group
    )

    return await item_service.create_menu_item(
        db=db, current_user=current_user, item_data=item_data, images=images
    )


# @router.post(
#     "/laundry-item-create",
#     status_code=status.HTTP_201_CREATED,
# )
# async def create_laundry_item(
#     name: str = Form(...),
#     description: str = Form(None),
#     price: Decimal = Form(...),
#     images: list[UploadFile] = File(...),
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> LaundryMenuResponseSchema:
#     """
#     Endpoint to create a new laundry item.
#     - Requires authenticated VENDOR user.
#     - The specified category_id must exist.
#     """

#     item_data = LaundryItemCreate(
#         name=name,
#         description=description,
#         price=price,
#         images=images,
#     )

#     return await item_service.create_laundry_item(
#         db=db, current_user=current_user, item_data=item_data, images=images
#     )



@router.get("/categories", status_code=status.HTTP_200_OK)
async def get_categories(
    db: AsyncSession = Depends(get_db),
) -> list[CategoryResponse]:
    """
    Endpoint to get all categories.
    """
    return await item_service.get_categories(db)


# @router.get(
#     "/{vendor_id}/restaurant",
#     status_code=status.HTTP_200_OK,
# )
# async def get_restaurant_menu(
#     vendor_id: UUID,
#     db: AsyncSession = Depends(get_db),
# ) -> list[RestaurantMenuResponseSchema]:
#     """
#     Endpoint to get all items for the logged-in VENDOR user.
#     """

#     return await item_service.get_restaurant_menu(db=db, vendor_id=vendor_id)


# @router.get(
#     "/{vendor_id}/lundry",
#     status_code=status.HTTP_200_OK,
# )
# async def get_lundry_menu(
#     vendor_id: UUID,
#     db: AsyncSession = Depends(get_db),
# ) -> list[LaundryMenuResponseSchema]:
#     """
#     Endpoint to get all items for the logged-in VENDOR user.
#     """

#     return await item_service.get_laundry_menu(db=db, user_id=vendor_id)


@router.get(
    "/{item_id}/item",
    status_code=status.HTTP_200_OK,
   
)
async def get_menu_item_by_id(
    item_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> RestaurantMenuResponseSchema | LaundryMenuResponseSchema:
    """
    Endpoint to retrieve a specific item by its UUID.
    - Requires authenticated VENDOR user.
    - Returns 404 if the item is not found or does not belong to the user.
    """
    return await item_service.get_menu_item_by_id(db, item_id)



@router.put(
    "/{item_id}/update",
    status_code=status.HTTP_202_ACCEPTED,
   
)
async def update_menu_item(
    item_id: UUID,
    item_data: MenuItemCreate, 
    db: AsyncSession = Depends(get_db),
    images: list[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
) -> RestaurantMenuResponseSchema | LaundryMenuResponseSchema:
    """
    Endpoint to update an existing item.
    - Requires authenticated VENDOR user.
    - Returns 404 if the item is not found or does not belong to the user.
    - Returns 404 if the target category_id does not exist.
    """
    return await item_service.update_menu_item(
        db,
        current_user,
        item_id,
        item_data,
        images=images,
    )


@router.delete(
    "/{item_id}/delete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Endpoint to delete an existing item.
    - Requires authenticated VENDOR user.
    - Returns 404 if the item is not found or does not belong to the user.
    - Returns 204 No Content on successful deletion.
    """
    await item_service.delete_item(db, current_user, item_id)
    return None
