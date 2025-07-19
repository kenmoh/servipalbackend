import json
from uuid import UUID
from typing import Optional

from fastapi import HTTPException, status, UploadFile, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload
from asyncpg.exceptions import UniqueViolationError
from sqlalchemy.exc import IntegrityError

from app.models.models import Item, User, Category, ItemImage
from app.schemas.product_schemas import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductImage,
)
from app.schemas.item_schemas import ItemType
from app.config.config import redis_client, settings
from app.utils.s3_service import upload_multiple_images


async def create_product(
    db: AsyncSession,
    product_data: ProductCreate,
    seller: User,
    images: list[UploadFile],
) -> ProductResponse:
    """
    Creates a new product listing for the given seller.

    Args:
        db: The database session.
        product_data: The product data from the request.
        seller: The authenticated user (seller).

    Returns:
        The newly created product.

    Raises:
        HTTPException: If the category is not found or creation fails.
    """
    # 1. Check if category exists
    category = await db.get(Category, product_data.category_id)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category with id {product_data.category_id} not found.",
        )

    try:
        # Create product first
        new_product = Item(
            **product_data.model_dump(), user_id=seller.id, item_type=ItemType.PRODUCT
        )
        db.add(new_product)
        await db.flush()

        # Upload images and create ItemImage records
        urls = await upload_multiple_images(
            images,
        )

        for url in urls:
            item_image = ItemImage(item_id=new_product.id, url=url)
            db.add(item_image)

        await db.commit()
        await db.refresh(new_product)

        redis_client.delete("all_products")
        redis_client.delete(f"products:{seller.id}")

        return new_product
    except IntegrityError as e:
        print
        await db.rollback()
        # Check if the error is due to the unique constraint violation
        if isinstance(e.orig, UniqueViolationError) and "uq_name_item" in str(e.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You already have an item with this name {product_data['name']}",
            )
    except Exception as e:
        await db.rollback()
        # Log the error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create item: {str(e)}",
        )


async def get_product_by_id(
    db: AsyncSession, product_id: UUID, background_task: BackgroundTasks | None = None
) -> ProductResponse | None:
    """
    Retrieves a single product by its ID, including seller and category info.
    Uses Redis caching to improve performance.

    Args:
        db: The database session.
        product_id: The ID of the product to retrieve.
        redis: Redis client instance.
        background_tasks: FastAPI background tasks for cache invalidation.

    Returns:
        The product details or None if not found.
    """
    cached_product = redis_client.get(f"product:{product_id}")
    if cached_product:
        return ProductResponse(**json.loads(cached_product))

    # Not in cache, query database
    stmt = (
        select(Item)
        .where(Item.id == product_id, Item.item_type == ItemType.PRODUCT)
        .options(selectinload(Item.images), selectinload(Item.category))
    )

    result = await db.execute(stmt)
    product = result.scalar_one_or_none()

    if not product:
        return None

    # Convert and cache the product
    product_response = convert_item_to_product_response(product)

    # Cache the formatted response
    if product_response:
        redis_client.setex(
            f"product:{product_id}",
            settings.REDIS_EX,
            json.dumps(product_response.model_dump(), default=str),
        )

    return product_response


async def get_products(db: AsyncSession) -> list[ProductResponse]:
    cache_key = "all_products"

    cached_products = redis_client.get(cache_key)
    if cached_products:
        print(f"Cache HIT for key: {cache_key}")
        return [ProductResponse(**product) for product in json.loads(cached_products)]

    # Get ALL products and cache them
    stmt = (
        select(Item)
        .where(Item.item_type == ItemType.PRODUCT)
        .options(selectinload(Item.images))
        .order_by(Item.created_at.desc())
    )
    result = await db.execute(stmt)
    products = result.scalars().all()

    product_responses = [
        convert_item_to_product_response(product) for product in products
    ]

    if product_responses:
        redis_client.setex(
            cache_key,
            settings.REDIS_EX,
            json.dumps(
                [product.model_dump() for product in product_responses], default=str
            ),
        )
        print(f"CACHE SET FOR {cache_key}")
    return product_responses


async def get_user_products(
    db: AsyncSession, current_user: User
) -> list[ProductResponse]:
    cache_key = f"products:{current_user.id}"

    cached_products = redis_client.get(cache_key)
    if cached_products:
        return [ProductResponse(**product) for product in json.loads(cached_products)]

    # Get ALL products and cache them
    stmt = (
        select(Item)
        .where(Item.item_type == ItemType.PRODUCT, Item.user_id == current_user.id)
        .options(selectinload(Item.images))
        .order_by(Item.created_at.desc())
    )
    result = await db.execute(stmt)
    products = result.scalars().all()

    product_responses = [
        convert_item_to_product_response(product) for product in products
    ]

    if product_responses:
        redis_client.setex(
            cache_key,
            settings.REDIS_EX,
            json.dumps(
                [product.model_dump() for product in product_responses], default=str
            ),
        )

    return product_responses


async def update_product(
    db: AsyncSession, product_id: UUID, product_data: ProductUpdate, current_user: User
) -> Optional[ProductResponse]:
    """
    Updates an existing product if the current user is the seller.

    Args:
        db: The database session.
        product_id: The ID of the product to update.
        product_data: The updated product data.
        current_user: The authenticated user attempting the update.

    Returns:
        The updated product details or None if not found/not authorized.

    Raises:
        HTTPException: If category not found, product not found, permission denied, or update fails.
    """
    product = await db.get(Item, product_id)

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item not found!",
        )

    if product.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this product",
        )

    update_values = product_data.model_dump(exclude_unset=True)

    # If category is being updated, check if the new one exists
    if "category_id" in update_values and update_values["category_id"] is not None:
        category = await db.get(Category, update_values["category_id"])
        if not category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category with id {update_values['category_id']} not found.",
            )

    # Update 'in_stock' based on 'stock' if 'stock' is provided
    if "stock" in update_values:
        update_values["in_stock"] = update_values["stock"] > 0

    if not update_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No update data provided"
        )

    stmt = (
        update(Item)
        .where(Item.id == product_id)
        .values(**update_values)
        .returning(Item)
    )

    try:
        result = await db.execute(stmt)
        updated_product = result.scalar_one()
        await db.commit()

        invalidate_product_cache(product_id, current_user.id)

        return updated_product
    except Exception as e:
        await db.rollback()
        # Log error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update product: {str(e)}",
        )


async def delete_product(
    db: AsyncSession, product_id: UUID, current_user: User
) -> bool:
    """
    Deletes a product if the current user is the seller.

    Args:
        db: The database session.
        product_id: The ID of the product to delete.
        current_user: The authenticated user attempting the deletion.

    Returns:
        True if deletion was successful, False otherwise.

    Raises:
        HTTPException: If product not found, permission denied, or deletion fails.
    """
    product = await db.get(Item, product_id)

    if not product:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Item not found",
        )

    if product.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this product",
        )

    stmt = delete(Item).where(Item.id == product_id)

    try:
        result = await db.execute(stmt)
        await db.commit()
        invalidate_product_cache(product_id, current_user.id)

        return result.rowcount > 0
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete product: {str(e)}",
        )


# <<<<< ---------- CACHE UTILITY FOR PRODUCTS ---------- >>>>>


def invalidate_product_cache(product_id: UUID, seller_id: UUID = None) -> None:
    """Invalidate product related caches"""
    redis_client.delete(f"product:{str(product_id)}")
    redis_client.delete("all_products")
    if seller_id:
        redis_client.delete(f"seller_products:{str(seller_id)}")


def convert_item_to_product_response(item) -> ProductResponse:
    """Convert SQLAlchemy Item to ProductResponse"""
    # Convert ItemImage objects to ProdutImage objects
    images = [
        ProductImage(id=img.id, url=img.url, item_id=img.item_id) for img in item.images
    ]

    return ProductResponse(
        id=item.id,
        user_id=item.user_id,  # This will be aliased to seller_id
        total_sold=item.total_sold,
        name=item.name,
        description=item.description or "",
        price=item.price,
        stock=item.stock,
        category_id=item.category_id,
        sizes=item.sizes,
        colors=item.colors if item.colors else [],
        in_stock=item.in_stock,
        images=images,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
