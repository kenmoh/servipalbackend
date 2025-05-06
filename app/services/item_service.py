import logging
from uuid import UUID
import json
from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update

from app.models.models import Item, Category, ItemImage, User
from app.schemas.item_schemas import (
    CategoryCreate,
    CategoryResponse,
    ItemCreate,
    ItemResponse,
)
from app.schemas.status_schema import AccountStatus, UserType
from app.config.config import redis_client
from app.utils.s3_service import delete_s3_object, upload_multiple_images


async def create_category(
    db: AsyncSession, current_user: User, data: CategoryCreate
) -> CategoryResponse:
    """Creates a new item category. Only VENDOR users can create categories."""

    if current_user.user_type != UserType.VENDOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )
    if (
        current_user.is_blocked
        or current_user.account_status != AccountStatus.CONFIRMED
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied! You have either been blocked or your account is not confirmed.",
        )
    try:
        # Check if category name already exists
        stmt_check = select(Category).where(Category.name == data.name)
        result_check = await db.execute(stmt_check)
        if result_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Category with name '{data.name}' already exists.",
            )

        new_category = Category(**data.model_dump())
        db.add(new_category)
        await db.commit()
        await db.refresh(new_category)

        # Invalidate categories cache
        invalidate_categories_cache()

        return new_category

    except Exception as e:
        await db.rollback()
        # Log the error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create category: {str(e)}",
        )


async def get_categories(db: AsyncSession) -> list[CategoryResponse]:
    """Retrieves all categories."""

    # Try cache first
    cached_categories = get_cached_categories()
    if cached_categories:
        return [CategoryResponse(**category) for category in cached_categories]

    # If not in cache, fetch from database
    stmt = select(Category)
    result = await db.execute(stmt)
    categories = result.scalars().all()

    # Cache the results if categories exist
    if categories:
        set_cached_categories(categories)

    return categories


async def create_item(
    db: AsyncSession, current_user: User, item_data: ItemCreate, images: list[UploadFile]
) -> ItemResponse:
    """Creates a new item for the current VENDOR user."""
    if current_user.user_type != UserType.VENDOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )
    if (
        current_user.is_blocked
        or current_user.account_status != AccountStatus.CONFIRMED
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied! You have either been blocked or your account is not confirmed.",
        )

    # Check if category exists
    stmt_cat = select(Category).where(Category.id == item_data.category_id)
    result_cat = await db.execute(stmt_cat)
    if not result_cat.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category with id {item_data.category_id} not found.",
        )

    try:
        # Create item first
        new_item = Item(**item_data.model_dump(), user_id=current_user.id)
        db.add(new_item)
        await db.flush()

        # Upload images and create ItemImage records
        urls = await upload_multiple_images(
            images,
            folder=f"items/{current_user.id}"
        )

        for url in urls:
            item_image = ItemImage(
                item_id=new_item.id,
                url=url
            )
            db.add(item_image)

        await db.commit()
        await db.refresh(new_item)

        redis_client.delete(f"vendor_items:{current_user.id}")

        return new_item
    except Exception as e:
        await db.rollback()
        # Log the error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create item: {str(e)}",
        )


async def get_items_by_current_user(
    db: AsyncSession, current_user: User
) -> list[ItemResponse]:
    """Retrieves all items belonging to the current VENDOR user."""
    if current_user.user_type != UserType.VENDOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    # Try cache first
    cached_items = redis_client.get(f"vendor_items:{current_user.id}")
    if cached_items:
        return json.loads(cached_items)

    stmt = select(Item).where(Item.user_id == current_user.id)
    result = await db.execute(stmt)
    items = result.scalars().all()

    # Cache the results
    redis_client.setex(
        f"vendor_items:{current_user.id}",
        CACHE_TTL,
        json.dumps([item.dict() for item in items], default=str)
    )
    return items


async def get_items_by_user_id(db: AsyncSession, user_id: UUID) -> list[ItemResponse]:
    """Retrieves all items belonging to  VENDOR user."""

    cached_items = redis_client.get(f"vendor_items:{user_id}")
    if cached_items:
        return json.loads(cached_items)

    stmt = select(Item).where(Item.user_id == user_id)
    result = await db.execute(stmt)
    items = result.scalars().all()

    # Cache the results if items exist
    if items:
        redis_client.setex(
            f"vendor_items:{user_id}",
            CACHE_TTL,
            json.dumps([item.dict() for item in items], default=str)
        )
    return items


async def get_item_by_id(
    db: AsyncSession, current_user: User, item_id: UUID
) -> ItemResponse:
    """Retrieves a specific item by ID belonging to the current VENDOR user."""
    if current_user.user_type != UserType.VENDOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

     # Try cache first
    cached_item = await get_cached_item(item_id)
    if cached_item and cached_item["user_id"] == str(current_user.id):
        return ItemResponse(**cached_item)

    stmt = select(Item).where(Item.id == item_id,
                              Item.user_id == current_user.id)
    result = await db.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
        )
    set_cached_item(item_id, item.dict())

    return item


async def update_item(
    db: AsyncSession, current_user: User, item_id: UUID, item_data: ItemCreate, images: list[UploadFile] = None
) -> ItemResponse:
    """ Updates an existing item belonging to the current VENDOR user.
        Handles both item data and image updates.
    """
    item = await get_item_by_id(
        db, current_user, item_id
    )  # Reuse get_item_by_id to check ownership and existence

    # Check if the new category exists if it's being changed
    if item_data.category_id != item.category_id:
        stmt_cat = select(Category).where(Category.id == item_data.category_id)
        result_cat = await db.execute(stmt_cat)
        if not result_cat.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with id {item_data.category_id} not found.",
            )

    # Update item fields
    update_data = item_data.model_dump(
        exclude_unset=True
    )  # Only update provided fields
    stmt = (
        update(Item)
        .where(Item.id == item_id)
        .values(**update_data)
        .returning(Item)  # Return the updated row
    )

    try:

        result = await db.execute(stmt)
        updated_item = result.scalar_one()

        # Handle image updates if provided
        if images:
            # Get existing image URLs
            old_images = await db.execute(
                select(ItemImage).where(ItemImage.item_id == item_id)
            )
            old_urls = [img.url for img in old_images.scalars().all()]

            # Upload new images
            new_urls = await upload_multiple_images(
                images,
                folder=f"items/{current_user.id}"
            )

            # Delete old images from database
            await db.execute(
                delete(ItemImage).where(ItemImage.item_id == item_id)
            )

            # Create new image records
            for url in new_urls:
                new_image = ItemImage(item_id=item_id, url=url)
                db.add(new_image)

            # Delete old images from S3
            for old_url in old_urls:
                await delete_s3_object(old_url)

        await db.commit()
        await db.refresh(updated_item)

        # Invalidate caches
        invalidate_item_cache(item_id)
        redis_client.delete(f"vendor_items:{current_user.id}")

        return updated_item

    except Exception as e:
        await db.rollback()
        # Log the error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update item: {str(e)}",
        )


async def delete_item(db: AsyncSession, current_user: User, item_id: UUID) -> None:
    """Deletes an item and its associated images belonging to the current VENDOR user."""
    # First, check if the item exists and belongs to the user
    item = await get_item_by_id(db, current_user, item_id)

    try:
        # Get all image URLs before deleting the item
        image_result = await db.execute(
            select(ItemImage).where(ItemImage.item_id == item_id)
        )
        item_images = image_result.scalars().all()

        # Delete item (this will cascade delete ItemImage records due to FK constraint)
        stmt = delete(Item).where(
            Item.id == item_id,
            Item.user_id == current_user.id
        )
        await db.execute(stmt)

        # Delete images from S3
        for image in item_images:
            await delete_s3_object(image.url)

        await db.commit()

        # Invalidate caches
        invalidate_item_cache(item_id)
        redis_client.delete(f"vendor_items:{current_user.id}")

        return None
    except Exception as e:
        await db.rollback()
        logging.error(f"Failed to delete item and images: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete item: {str(e)}"
        )

# <<<<< ---------- CACHE UTILITY FOR ITEM ---------- >>>>>
CACHE_TTL = 3600


def get_cached_item(item_id: UUID) -> dict:
    """Get item from cache"""
    cached_item = redis_client.get(f"item:{str(item_id)}")
    return json.loads(cached_item) if cached_item else None


def set_cached_item(item_id: UUID, item_data: dict) -> None:
    """Set item in cache"""
    redis_client.setex(
        f"item:{str(item_id)}",
        CACHE_TTL,
        json.dumps(item_data, default=str)
    )


def invalidate_item_cache(item_id: UUID) -> None:
    """Invalidate item cache"""
    redis_client.delete(f"item:{str(item_id)}")
    redis_client.delete(f"vendor_items:{str(item_id)}")
    redis_client.delete("all_items")


def get_cached_categories() -> list:
    """Get all categories from cache"""
    cached_categories = redis_client.get("all_categories")
    return json.loads(cached_categories) if cached_categories else None


def set_cached_categories(categories: list) -> None:
    """Set categories in cache"""
    redis_client.setex(
        "all_categories",
        CACHE_TTL,
        json.dumps([category.dict() for category in categories], default=str)
    )


def invalidate_categories_cache() -> None:
    """Invalidate categories cache"""
    redis_client.delete("all_categories")
