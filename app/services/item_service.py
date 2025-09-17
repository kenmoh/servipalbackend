import logging
from uuid import UUID
import json
from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select, delete, update, and_
from sqlalchemy.exc import IntegrityError
import asyncpg.exceptions

from app.models.models import Item, Category, ItemImage, User
from app.schemas.item_schemas import (
    CategoryCreate,
    CategoryResponse,
    FoodGroup,
    MenuResponseSchema,
    MenuItemCreate,
    ItemType,
    CategoryType,
    LaundryItemCreate,
    LaundryMenuResponseSchema
)
from app.schemas.status_schema import AccountStatus, UserType
from app.config.config import redis_client, settings
from app.utils.logger_config import setup_logger
from app.utils.s3_service import delete_s3_object, upload_multiple_images

logger = setup_logger()


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

        new_category = Category(**data.model_dump(), category_type=CategoryType.PRODUCT)
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
        return cached_categories

    # If not in cache, fetch from database
    stmt = select(Category)
    result = await db.execute(stmt)
    categories = result.scalars().all()

    # Cache the results if categories exist
    if categories:
        set_cached_categories(categories)

    return categories

async def get_category(db: AsyncSession, category_id: UUID) -> CategoryResponse:
    """Retrieves a category."""

    stmt = select(Category).where(Category.id == category_id)
    result = await db.execute(stmt)
    category = result.scalar_one_or_none()

    return category


async def create_menu_item(
    db: AsyncSession,
    current_user: User,
    item_data: MenuItemCreate,
    images: list[UploadFile],
) -> MenuResponseSchema:
    """Creates a new item for the current VENDOR user."""
    if current_user.user_type != UserType.RESTAURANT_VENDOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Restaurant vendors are allowed to perform this action ",
        )
    if (
        current_user.is_blocked
        or current_user.account_status != AccountStatus.CONFIRMED
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied! You have either been blocked or your account is not confirmed.",
        )
    if (
        current_user.user_type == UserType.RESTAURANT_VENDOR
        and not current_user.profile.business_name
        or not current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please update your profile",
        )

    try:
        # Create item first
        new_item = Item(**item_data.model_dump(), user_id=current_user.id, item_type=ItemType.FOOD)
        db.add(new_item)
        await db.flush()

        # Upload images and create ItemImage records
        urls = await upload_multiple_images(
            images,
        )

        for url in urls:
            item_image = ItemImage(item_id=new_item.id, url=url)
            db.add(item_image)

        await db.commit()
        await db.refresh(new_item)

        redis_client.delete(f"vendor_items:{current_user.id}")
        redis_client.delete(f"restaurant_menu:{current_user.id}:{item_data.food_group}")
        redis_client.delete(f"laundry_menu:{current_user.id}")

        return new_item

    except IntegrityError as e:
        # Check if it's a UniqueViolationError
        if isinstance(e.orig, asyncpg.exceptions.UniqueViolationError):
            if "uq_name_user_non_package" in str(
                e
            ) or "uq_name_user_non_package" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="You already have an item with this name.",
                )
        # If it's a different integrity error, let it fall through to the general exception
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database integrity error: {str(e)}",
        )

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create item: {str(e)}",
        )



async def create_laundry_item(
    db: AsyncSession,
    current_user: User,
    item_data: LaundryItemCreate,
    images: list[UploadFile],
) -> LaundryMenuResponseSchema:
    """Creates a new item for the current VENDOR user."""
    if current_user.user_type != UserType.LAUNDRY_VENDOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only laundry vendors are allowed to perform this action ",
        )
    if (
        current_user.is_blocked
        or current_user.account_status != AccountStatus.CONFIRMED
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied! You have either been blocked or your account is not confirmed.",
        )
    if (
        current_user.user_type == UserType.LAUNDRY_VENDOR
        and not current_user.profile.business_name
        or not current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please update your profile",
        )

    try:
        # Create item first
        new_item = Item(**item_data.model_dump(), user_id=current_user.id, item_type=ItemType.LAUNDRY)
        db.add(new_item)
        await db.flush()

        # Upload images and create ItemImage records
        urls = await upload_multiple_images(images)

        for url in urls:
            item_image = ItemImage(item_id=new_item.id, url=url)
            db.add(item_image)

        await db.commit()
        await db.refresh(new_item)

        # redis_client.delete(f"vendor_items:{current_user.id}")
        redis_client.delete(f"laundry_menu:{current_user.id}")

        return new_item

    except IntegrityError as e:
        # Check if it's a UniqueViolationError
        if isinstance(e.orig, asyncpg.exceptions.UniqueViolationError):
            if "uq_name_user_non_package" in str(
                e
            ) or "uq_name_user_non_package" in str(e):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="You already have an item with this name.",
                )
        # If it's a different integrity error, let it fall through to the general exception
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database integrity error: {str(e)}",
        )

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create item: {str(e)}",
        )



async def get_restaurant_menu(
    db: AsyncSession, vendor_id: UUID, food_group: FoodGroup = FoodGroup.MAIN_COURSE
) -> list[MenuResponseSchema]:
    """
    Get restaurant menu items with their individual reviews.
    This is for when customer visits a specific restaurant.
    """
    try:
        # Try cache first
        # cached_menu = get_cached_menu(vendor_id, food_group)
        key = f"restaurant_menu:{vendor_id}:{food_group}"
        cached_menu = redis_client.get(key)
        if cached_menu:
            menu = json.loads(cached_menu)
            return [MenuResponseSchema(**m) for m in menu]

        # Get menu items with their reviews
        menu_query = (
            select(Item)
            .where(Item.is_deleted == False)
            .where(
                Item.user_id == vendor_id,
                Item.item_type == ItemType.FOOD,
                Item.food_group == food_group,
            )
            .order_by(Item.name)
        )

        result = await db.execute(menu_query)
        menu_items = result.scalars().all()

        # Format menu with reviews
        menu_response = []
        for item in menu_items:
            # category = await get_category(db=db, category_id=item.category_id)
            menu_response.append(
                {
                    "id": str(item.id),
                    "restaurant_id": item.user_id,
                    "name": item.name,
                    "description": item.description,
                    "price": str(item.price),
                    "item_type": item.item_type,
                    "food_group": item.food_group,
                    "is_deleted": item.is_deleted,
                    "category_id": item.category_id,
                    "images": [
                        {"id": img.id, "url": img.url, "item_id": img.item_id}
                        for img in item.images
                    ],
                }
            )

        # Cache the menu
        # set_cached_menu(vendor_id, food_group, menu_response)
        redis_client.setex(key, settings.REDIS_EX, json.dumps(menu_response, default=str))

        return MenuResponseSchema(**menu_response)

    except Exception as e:
        logger.error(f"Error fetching restaurant menu {vendor_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch restaurant menu",
        )


async def get_laundry_menu(
    db: AsyncSession,
    vendor_id: UUID,
) -> list[LaundryMenuResponseSchema]:
    """
    Get restaurant menu items with their individual reviews.
    This is for when customer visits a specific restaurant.
    """
    try:
        # Try cache first
        key = f"laundry_menu:{vendor_id}"
        cached_menu = redis_client.get(key)
        if cached_menu:
            return [LaundryMenuResponseSchema(**m) for m in json.loads(cached_menu)]

        # Get menu items with their reviews
        menu_query = (
            select(Item)
            .where(
                Item.user_id == vendor_id,
                Item.item_type == ItemType.LAUNDRY,
                Item.is_deleted == False
            )
            .order_by(Item.name)
        )

        result = await db.execute(menu_query)
        menu_items = result.scalars().all()

        # Format menu with reviews
        menu_response = []
        for item in menu_items:
            menu_response.append(
                {
                    "id": str(item.id),
                    "laundry_id": item.user_id,
                    "name": item.name,
                    "description": item.description or None,
                    "price": str(item.price),
                    "item_type": item.item_type,
                    "images": [
                        {"id": img.id, "url": img.url, "item_id": img.item_id}
                        for img in item.images
                    ],
                }
            )

        # Cache the menu
        redis_client.setex(
            key, settings.REDIS_EX, json.dumps(menu_response, default=str)
        )

        return [LaundryMenuResponseSchema(**menu) for menu in menu_response]

    except Exception as e:
        logger.error(f"Error fetching restaurant menu {vendor_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch restaurant menu",
        )


async def get_all_food_items(db: AsyncSession) -> list[MenuResponseSchema]:
    """
    Get all food items from all vendors.
    """
    try:
        # Try cache first
        cached_foods = redis_client.get("all_food_items")
        if cached_foods:
            return [MenuResponseSchema(**m) for m in json.loads(cached_foods)]

        # Get all food items
        food_query = (
            select(Item)
            .where(Item.item_type == ItemType.FOOD)
            .options(selectinload(Item.images))
            .order_by(Item.name)
        )

        result = await db.execute(food_query)
        food_items = result.unique().scalars().all()

        # Format response
        food_response = []
        for item in food_items:
            # category = await get_category(db=db, category_id=item.category_id)
            food_response.append(
                {
                    "id": str(item.id),
                    "user_id": item.user_id,
                    "name": item.name,
                    "description": item.description,
                    "price": str(item.price),
                    "item_type": item.item_type,
                    "food_group": item.food_group,
                    "category_id": item.category_id,
                    "images": [
                        {"id": img.id, "url": img.url, "item_id": img.item_id}
                        for img in item.images
                    ],
                }
            )

        # Cache the response
        redis_client.setex(
            "all_food_items", settings.REDIS_EX, json.dumps(food_response, default=str)
        )

        return [MenuResponseSchema(**food) for food in food_response]

    except Exception as e:
        logger.error(f"Error fetching all food items: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch food items",
        )


async def get_all_laundry_items(db: AsyncSession) -> list[LaundryMenuResponseSchema]:
    """
    Get all laundry items from all vendors.
    """
    try:
        # Try cache first
        cached_laundries = redis_client.get("all_laundry_items")
        if cached_laundries:
            return [MenuResponseSchema(**m) for m in json.loads(cached_laundries)]

        # Get all laundry items
        laundry_query = (
            select(Item)
            .where(Item.item_type == ItemType.LAUNDRY, Item.is_deleted==False)
            .options(selectinload(Item.images))
            .order_by(Item.name)
        )

        result = await db.execute(laundry_query)
        laundry_items = result.unique().scalars().all()

        # Format response
        laundry_response = []
        for item in laundry_items:
            laundry_response.append(
                {
                    "id": str(item.id),
                    "user_id": item.user_id,
                    "name": item.name,
                    "description": item.description or None,
                    "price": str(item.price),
                    "item_type": item.item_type,
                    "images": [
                        {"id": img.id, "url": img.url, "item_id": img.item_id}
                        for img in item.images
                    ],
                }
            )

        # Cache the response
        redis_client.setex(
            "all_laundry_items",
            settings.REDIS_EX,
            json.dumps(laundry_response, default=str),
        )

        return [LaundryMenuResponseSchema(**laundry) for laundry in laundry_response]

    except Exception as e:
        logger.error(f"Error fetching all laundry items: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch laundry items",
        )


async def get_menu_item_by_id(
    db: AsyncSession, menu_item_id: UUID
) -> MenuResponseSchema:
    """Retrieves a specific item by ID belonging to the current VENDOR user."""
    cache_key = f"item:{menu_item_id}"
    
    # Try cache first
    cached_item = redis_client.get(cache_key)
    if cached_item:
        item_dict = json.loads(cached_item)
        return MenuResponseSchema(**item_dict)
    
    # Query database
    stmt = (
        select(Item).where(Item.id == menu_item_id).options(selectinload(Item.images))
    )
    result = await db.execute(stmt)
    menu_item = result.unique().scalar_one_or_none()
    
    if not menu_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
        )
    
    # Prepare dict for caching and response
    item_dict = {
        "id": str(menu_item.id),
        "vendor_id": str(menu_item.user_id),
        "name": menu_item.name,
        "description": menu_item.description or None,
        "price": str(menu_item.price),
        "item_type": menu_item.item_type,
        "user_id": menu_item.user_id,
        "category_id": menu_item.category_id,
        "side": menu_item.side,
        "food_group": menu_item.food_group or None,
        "images": [
            {
                "id": str(img.id),
                "url": img.url, 
                "item_id": str(img.item_id)
            }
            for img in menu_item.images
        ],
    }
    
    # Cache the serialized item_dict (not the menu_item object)
    redis_client.setex(cache_key, settings.REDIS_EX, json.dumps(item_dict, default=str))
    
    # Return response model
    return MenuResponseSchema(**item_dict)

async def update_menu_item(
    db: AsyncSession,
    current_user: User,
    menu_item_id: UUID,
    item_data: MenuItemCreate,
    images: list[UploadFile] = None,
) -> MenuResponseSchema:
    """Updates an existing item belonging to the current VENDOR user.
    Handles both item data and image updates.
    """
    cache_key = f"restaurant_menu:{current_user.id}:{item_data.food_group}"
  
    # Fetch the current item from DB (not cache, to ensure accuracy)
    db_item = await get_item_by_id(
        db=db, item_id=menu_item_id, current_user=current_user
    )
    if not db_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
        )

    update_data = item_data.model_dump(exclude_unset=True)

    # Only check for duplicate name if name is being changed
    new_name = update_data.get("name")
    if new_name and new_name != db_item.name:
        # Check if another item with this name exists for this user
        duplicate_stmt = select(Item).where(
            and_(
                Item.user_id == current_user.id,
                Item.name == new_name,
                Item.id != menu_item_id,
            )
        )
        duplicate_result = await db.execute(duplicate_stmt)
        duplicate_item = duplicate_result.scalar_one_or_none()
        if duplicate_item:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already have an item with this name.",
            )

    stmt = (
        update(Item)
        .where(Item.id == menu_item_id, Item.user_id == current_user.id)
        .values(**update_data)
        .returning(Item)
    )

    try:
        result = await db.execute(stmt)
        updated_item = result.scalar_one()

        # Handle image updates if provided
        if images:
            # Get existing image URLs
            old_images = await db.execute(
                select(ItemImage).where(ItemImage.item_id == menu_item_id)
            )
            old_urls = [img.url for img in old_images.scalars().all()]

            # Upload new images
            new_urls = await upload_multiple_images(images)

            # Delete old images from database
            await db.execute(delete(ItemImage).where(ItemImage.item_id == menu_item_id))

            # Create new image records
            for url in new_urls:
                new_image = ItemImage(item_id=menu_item_id, url=url)
                db.add(new_image)

            # Delete old images from S3
            for old_url in old_urls:
                await delete_s3_object(old_url)

        await db.commit()
        await db.refresh(updated_item)

        # Invalidate caches
        invalidate_item_cache(menu_item_id)
        redis_client.delete(cache_key)
        

        return updated_item

    except IntegrityError as e:
        # Check if it's a UniqueViolationError for item name per user
        if hasattr(e, "orig") and (
            "uq_name_user_non_package" in str(e)
            or "unique_name_user_non_package" in str(e)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already have an item with this name.",
            )
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database integrity error: {str(e)}",
        )

    except Exception as e:
        await db.rollback()
        # Log the error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update menu item: {str(e)}",
        )



async def update_laumdry_item(
    db: AsyncSession,
    current_user: User,
    item_id: UUID,
    item_data: LaundryItemCreate,
    images: list[UploadFile] = None,
) -> LaundryMenuResponseSchema:
    """Updates an existing item belonging to the current VENDOR user.
    Handles both item data and image updates.
    """
    cache_key = f"laundry_item:{current_user.id}"
  
    # Fetch the current item from DB (not cache, to ensure accuracy)
    db_item = await get_item_by_id(
        db=db, item_id=item_id, current_user=current_user
    )
    if not db_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
        )

    update_data = item_data.model_dump(exclude_unset=True)

    # Only check for duplicate name if name is being changed
    new_name = update_data.get("name")
    if new_name and new_name != db_item.name:
        # Check if another item with this name exists for this user
        duplicate_stmt = select(Item).where(
            and_(
                Item.user_id == current_user.id,
                Item.name == new_name,
                Item.id != item_id,
            )
        )
        duplicate_result = await db.execute(duplicate_stmt)
        duplicate_item = duplicate_result.scalar_one_or_none()
        if duplicate_item:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already have an item with this name.",
            )

    stmt = (
        update(Item)
        .where(Item.id == item_id, Item.user_id == current_user.id)
        .values(**update_data)
        .returning(Item)
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
            new_urls = await upload_multiple_images(images)

            # Delete old images from database
            await db.execute(delete(ItemImage).where(ItemImage.item_id == item_id))

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
        redis_client.delete(cache_key)
        

        return updated_item

    except IntegrityError as e:
        # Check if it's a UniqueViolationError for item name per user
        if hasattr(e, "orig") and (
            "uq_name_user_non_package" in str(e)
            or "unique_name_user_non_package" in str(e)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You already have an item with this name.",
            )
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database integrity error: {str(e)}",
        )

    except Exception as e:
        await db.rollback()
        # Log the error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update menu item: {str(e)}",
        )



async def delete_item(db: AsyncSession, current_user: User, item_id: UUID) -> None:
    """Deletes an item and its associated images belonging to the current VENDOR user."""
    # Check cache for item
    # cache_key = f"item:{item_id}"

    # cached_item = get_cached_item(cache_key)
    # if cached_item:
    #     if cached_item.get("user_id") != str(current_user.id):
    #         raise HTTPException(
    #             status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
    #         )
    #     # Continue to delete in DB for consistency
    # else:
    #     # Not in cache, query DB
    #     item = await get_item_by_id(db=db, current_user=current_user, item_id=item_id)
    #     if not item or item.user_id != current_user.id:
    #         raise HTTPException(
    #             status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
    #         )

    #     food_group = item.food_group

    try:
        # Get all image URLs before deleting the item
        item = await get_item_by_id(db=db, current_user=current_user, item_id=item_id)
        food_group = item.food_group

        image_result = await db.execute(
            select(ItemImage).where(ItemImage.item_id == item.id)
        )
        item_images = image_result.scalars().all()

        # Delete item (this will cascade delete ItemImage records due to FK constraint)
        # stmt = delete(Item).where(Item.id == item_id, Item.user_id == current_user.id)
        # await db.execute(stmt)

        # Delete images from S3
        for image in item_images:
            await delete_s3_object(image.url)

        item.is_deleted = True

        await db.commit()

        # Invalidate caches
        invalidate_item_cache(item_id)
        redis_client.delete(cache_key)
        redis_client.delete(f"vendor_items:{current_user.id}")
        redis_client.delete(f"restaurant_menu:{current_user.id}:{food_group}")


        return None
    except Exception as e:
        await db.rollback()
        logging.error(f"Failed to delete item and images: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete item: {str(e)}",
        )


async def get_item_by_id(db: AsyncSession, item_id: UUID, current_user: User):
    stmt = (
        select(Item)
        .where(Item.id == item_id, Item.user_id == current_user.id)
        .options(selectinload(Item.images), selectinload(Item.reviews))
    )
    result = await db.execute(stmt)

    return result.scalar_one_or_none()


# <<<<< ---------- CACHE UTILITY FOR ITEM ---------- >>>>>


def get_cached_item(item_id: UUID) -> dict:
    """Get item from cache"""
    cached_item = redis_client.get(f"item:{str(item_id)}")
    return json.loads(cached_item) if cached_item else None


def set_cached_item(item_id: UUID, item_data: dict) -> None:
    """Set item in cache"""
    redis_client.setex(
        f"item:{str(item_id)}", settings.REDIS_EX, json.dumps(item_data, default=str)
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
    categories_dict_list = [
        {
            "id": category.id,
            "name": category.name,
            "category_type": category.category_type,
        }
        for category in categories
    ]
    redis_client.setex(
        "all_categories",
        settings.REDIS_EX,
        json.dumps(categories_dict_list, default=str),
    )


def invalidate_categories_cache() -> None:
    """Invalidate categories cache"""
    redis_client.delete("all_categories")


# <<<<< ---------- CACHE UTILITY FOR MENU ---------- >>>>>
def get_cached_menu(vendor_id: UUID, food_group: FoodGroup) -> dict:
    key = f"menu:{vendor_id}:{food_group}"
    cached_menu = redis_client.get(key)
    return json.loads(cached_menu) if cached_menu else None


def set_cached_menu(vendor_id: UUID, food_group: FoodGroup, menu: dict) -> None:
    key = f"menu:{vendor_id}:{food_group}"
    redis_client.setex(key, settings.REDIS_EX, json.dumps(menu, default=str))


def invalidate_menu_cache(vendor_id: UUID, food_group: FoodGroup) -> None:
    key = f"menu:{vendor_id}:{food_group}"
    redis_client.delete(key)
