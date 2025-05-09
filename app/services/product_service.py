import json
from uuid import UUID
from typing import List, Optional

from fastapi import HTTPException, status, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload

from app.models.models import Item, User, Category, ItemImage
from app.schemas.product_schemas import ProductCreate, ProductUpdate, ProductResponse
from app.schemas.item_schemas import ItemType
from app.config.config import redis_client
from app.utils.s3_service import upload_multiple_images


async def create_product(
    db: AsyncSession, product_data: ProductCreate, seller: User, images: list[UploadFile]
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

    # 2. Create Item instance
    new_product = Item(
        **product_data.model_dump(exclude_unset=True),
        seller_id=seller.id,
    )

    try:
        # Create product first
        new_product = Item(**product_data.model_dump(), user_id=current_user.id, item_type=ItemType.PRODUCT)
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
        redis_client.delete(f"seller_products:{seller.id}")

        return new_product
    except Exception as e:
        await db.rollback()
        # Log the error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create item: {str(e)}",
        )


async def get_product_by_id(db: AsyncSession, product_id: UUID) -> ProductResponse:
    """
    Retrieves a single product by its ID, including seller and category info.

    Args:
        db: The database session.
        product_id: The ID of the product to retrieve.

    Returns:
        The product details or None if not found.
    """
    cached_product = await get_cached_product(product_id)
    if cached_product:
        return ProductResponse(**cached_product)
    stmt = (
        select(Item)
        .where(Item.id == product_id)
        # Eager load relationships
        .options(selectinload(Item.seller), selectinload(Item.category))
    )

    result = await db.execute(stmt)
    product = result.scalar_one_or_none()

    if product:
        await set_cached_product(product_id, product.dict())

    return product


async def get_products(
    db: AsyncSession, skip: int = 0, limit: int = 100
) -> List[ProductResponse]:
    """
    Retrieves a list of products with pagination.

    Args:
        db: The database session.
        skip: Number of products to skip.
        limit: Maximum number of products to return.

    Returns:
        A list of products.
    """

    cache_key = f"all_products:{skip}:{limit}"
    cached_products = redis_client.get(cache_key)
    if cached_products:
        return [ProductResponse(**p) for p in json.loads(cached_products)]

    stmt = select(Item).offset(skip).limit(limit).order_by(Item.created_at.desc())
    result = await db.execute(stmt)
    products = result.scalars().all()

    if products:
        redis_client.setex(
            cache_key, CACHE_TTL, json.dumps([p.dict() for p in products], default=str)
        )
    return products


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

    if product.seller_id != current_user.id:
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

    if product.seller_id != current_user.id:
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

# Add cache constants and helpers at the top after imports
CACHE_TTL = 3600  # 1 hour


def get_cached_product(product_id: UUID) -> Optional[dict]:
    """Get product from cache"""
    cached_product = redis_client.get(f"product:{str(product_id)}")
    return json.loads(cached_product) if cached_product else None


def set_cached_product(product_id: UUID, product_data: dict) -> None:
    """Set product in cache"""
    redis_client.setex(
        f"product:{str(product_id)}", CACHE_TTL, json.dumps(product_data, default=str)
    )


def invalidate_product_cache(product_id: UUID, seller_id: UUID = None) -> None:
    """Invalidate product related caches"""
    redis_client.delete(f"product:{str(product_id)}")
    redis_client.delete("all_products")
    if seller_id:
        redis_client.delete(f"seller_products:{str(seller_id)}")
