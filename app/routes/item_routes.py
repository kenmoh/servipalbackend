from uuid import UUID
from typing import List, Annotated
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_db, get_current_user
from app.models.models import User
from app.schemas.item_schemas import (
    CategoryCreate,
    CategoryResponse,
    ItemCreate,
    ItemResponse,
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
    "",
    response_model=ItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new item",
    description="Allows VENDOR users to create a new item associated with their account and a category.",
)
async def create_new_item(
    name: str = Form(...),
    description: str = Form(None),
    price: Decimal = Form(...),
    item_type: ItemType = Form(...),
    category_id: UUID | None = Form(None),
    images: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemResponse:
    """
    Endpoint to create a new item.
    - Requires authenticated VENDOR user.
    - The specified category_id must exist.
    """

    item_data = ItemCreate(
        name=name,
        description=description,
        price=price,
        item_type=item_type,
        images=images,
        category_id=category_id,
    )

    return await item_service.create_item(
        db=db, current_user=current_user, item_data=item_data, images=images
    )


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=List[ItemResponse],
    summary="Get items for current user",
    description="Retrieves all items belonging to the currently authenticated VENDOR user.",
)
async def read_user_items(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[ItemResponse]:
    """
    Endpoint to get all items for the logged-in VENDOR user.
    """
    return await item_service.get_items_by_current_user(db, current_user)


@router.get("/categories", status_code=status.HTTP_200_OK)
async def get_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[CategoryResponse]:
    """
    Endpoint to get all categories.
    """
    return await item_service.get_categories(db)


@router.get(
    "/{vendor_id}",
    summary="Get vendor items",
    description="Retrieves all items belonging to a VENDOR user.",
    status_code=status.HTTP_200_OK,
)
async def read_vendor_items(
    vendor_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> List[ItemResponse]:
    """
    Endpoint to get all items for the logged-in VENDOR user.
    """

    return await item_service.get_items_by_user_id(db=db, user_id=vendor_id)


@router.get(
    "/{item_id}/item",
    response_model=ItemResponse,
    tags=["Items"],
    summary="Get a specific item by ID",
    status_code=status.HTTP_200_OK,
    description="Retrieves a specific item by its ID, if it belongs to the currently authenticated VENDOR user.",
)
async def read_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemResponse:
    """
    Endpoint to retrieve a specific item by its UUID.
    - Requires authenticated VENDOR user.
    - Returns 404 if the item is not found or does not belong to the user.
    """
    item = await item_service.get_item_by_id(db, item_id)
    if not item:
        # This check might be redundant if get_item_by_id already raises HTTPException
        # but it's good practice for clarity.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
        )
    return item


@router.put(
    "/{item_id}",
    response_model=ItemResponse,
    tags=["Items"],
    summary="Update an item",
    status_code=status.HTTP_202_ACCEPTED,
    description="Updates an existing item belonging to the currently authenticated VENDOR user.",
)
async def update_existing_item(
    item_id: UUID,
    item_data: ItemCreate,  # Using ItemCreate for update allows changing all fields
    db: AsyncSession = Depends(get_db),
    images: list[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
) -> ItemResponse:
    """
    Endpoint to update an existing item.
    - Requires authenticated VENDOR user.
    - Returns 404 if the item is not found or does not belong to the user.
    - Returns 404 if the target category_id does not exist.
    """
    return await item_service.update_item(
        db,
        current_user,
        item_id,
        item_data,
        images=images,
    )


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an item",
    description="Deletes an existing item belonging to the currently authenticated VENDOR user.",
)
async def delete_existing_item(
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
    return None  # Return None for 204 status code
