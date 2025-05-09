from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID

# Assuming these dependencies exist and are correctly defined elsewhere
# e.g., in app/dependencies.py
from app.database.database import get_db
from app.auth.auth import get_current_user
from app.models.models import User
from app.schemas.product_schemas import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
)

# Assuming the service functions are correctly defined in app/services/product_service.py
from app.services import product_service

router = APIRouter(prefix="/products", tags=["Products"])


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new product listing",
    description="Allows an authenticated user to list a new product for sale.",
)
async def create_new_product(
    product_in: ProductCreate=Depends(),
    images: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Endpoint to create a new product. Requires authentication.
    The logged-in user will be set as the seller.
    """
    # The service function handles checking category existence and creation logic
    product = await product_service.create_product(
        db=db, product_data=product_in, seller=current_user
    )

    return product


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a specific product by ID",
    description="Retrieves detailed information about a single product.",
)
async def read_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint to retrieve a product by its unique ID. Publicly accessible.
    """
    product = await product_service.get_product_by_id(db=db, product_id=product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    return product


@router.get(
    "",
    response_model=List[ProductResponse],
    status_code=status.HTTP_200_OK,
    summary="List products",
    description="Retrieves a list of products with optional pagination.",
)
async def read_products(
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(
        100, ge=1, le=200, description="Maximum number of items to return"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint to retrieve a list of products. Supports pagination. Publicly accessible.
    """
    products = await product_service.get_products(db=db, skip=skip, limit=limit)
    return products


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a product",
    description="Allows the seller of a product to update its details.",
)
async def update_existing_product(
    product_id: UUID,
    product_in: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Endpoint to update an existing product. Requires authentication,
    and the user must be the seller of the product.
    """
    # Service function handles checking ownership, category existence, and update logic
    updated_product = await product_service.update_product(
        db=db, product_id=product_id, product_data=product_in, current_user=current_user
    )
    if updated_product is None:
        # This case is hit if the product wasn't found initially in the service
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    # Other exceptions (403 Forbidden, 404 Category Not Found, 500 DB error) handled in service
    return updated_product


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a product",
    description="Allows the seller of a product to delete it.",
)
async def delete_existing_product(
    product_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Endpoint to delete a product. Requires authentication,
    and the user must be the seller of the product.
    """
    # Service function handles checking ownership and deletion logic
    deleted = await product_service.delete_product(
        db=db, product_id=product_id, current_user=current_user
    )
    if not deleted:
        # This case is hit if the product wasn't found initially in the service
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
