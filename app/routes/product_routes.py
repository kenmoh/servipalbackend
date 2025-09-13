from decimal import Decimal
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
    UploadFile,
    File,
    BackgroundTasks,
    Form,
)
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
from app.utils.limiter import limiter

router = APIRouter(prefix="/api/products", tags=["Products"])


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new product listing",
    description="Allows an authenticated user to list a new product for sale.",
)
@limiter.limit("5/minute")
async def create_new_product(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    price: Decimal = Form(...),
    stock: int = Form(...),
    category_id: UUID = Form(...),
    sizes: Optional[str] = Form(None),
    colors: Optional[List[str]] = Form(None),
    images: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Endpoint to create a new product. Requires authentication.
    The logged-in user will be set as the seller.
    """

    product_data = ProductCreate(
        name=name,
        description=description,
        price=price,
        stock=stock,
        category_id=category_id,
        sizes=sizes,
        colors=colors,
    )
    # The service function handles checking category existence and creation logic
    product = await product_service.create_product(
        db=db, product_data=product_data, seller=current_user, images=images
    )

    return product


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a specific product by ID",
    description="Retrieves detailed information about a single product.",
)
async def get_product_by_id(
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


# @router.get(
#     "",
#     response_model=List[ProductResponse],
#     status_code=status.HTTP_200_OK,
#     summary="List products",
#     description="Retrieves a list of products",
# )
# async def read_products(
#     db: AsyncSession = Depends(get_db),
# ):
#     """
#     Endpoint to retrieve a list of products.
#     """
#     return await product_service.get_products(db=db)


@router.get(
    "",
    response_model=List[ProductResponse],
    status_code=status.HTTP_200_OK,
    summary="List products",
    description="Retrieves a list of products by category",
)
async def read_products(
    category_id: UUID = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint to retrieve a list of products.
    """
    return await product_service.get_products_by_category(
        db=db, category_id=category_id
    )


@router.get(
    "/{user_id}/user-products",
    response_model=List[ProductResponse],
    status_code=status.HTTP_200_OK,
)
async def get_user_items(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint to retrieve a list of products. Supports pagination. Publicly accessible.
    """
    return await product_service.get_user_products(db=db, user_id=user_id)


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a product",
    description="Allows the seller of a product to update its details.",
)
async def update_existing_product(
    product_id: UUID,
    name: str = Form(...),
    description: str = Form(...),
    price: Decimal = Form(...),
    stock: int = Form(...),
    category_id: UUID = Form(...),
    sizes: Optional[str] = Form(None),
    colors: Optional[List[str]] = Form(None),
    images: Optional[list[UploadFile]] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Endpoint to update an existing product. Requires authentication,
    and the user must be the seller of the product.
    """
    # Service function handles checking ownership, category existence, and update logic

    # Convert empty strings to None and handle colors parsing
    processed_sizes = sizes if sizes and sizes.strip() else None
    
    # Parse colors from comma-separated string or None if empty
    processed_colors = None
    if colors and colors.strip():
        processed_colors = [color.strip() for color in colors.split(",") if color.strip()]

    product_data = ProductCreate(
        name=name,
        description=description,
        price=price,
        stock=stock,
        category_id=category_id,
        sizes=processed_sizes,
        colors=processed_colors,
    )

    updated_product = await product_service.update_product(
        db=db,
        images=images,
        product_id=product_id,
        product_data=product_data,
        current_user=current_user,
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
