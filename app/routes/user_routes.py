from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status


from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import create_tokens, get_current_user
from app.database.database import get_db
from app.models.models import ProfileImage, User, Profile

from app.schemas.schemas import DispatchRiderSchema
from app.schemas.status_schema import UserType
from app.schemas.user_schemas import (
    ProfileSchema,
    UserProfileResponse,
    UserResponse,
    WalletRespose,
    WalletSchema,
    VendorUserResponse,
    ProfileImageResponseSchema,
    CreateReviewSchema,
)
from app.services import user_service
from app.schemas.item_schemas import MenuWithReviewResponseSchema
from app.utils.s3_service import add_profile_image, update_image


router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("", status_code=status.HTTP_200_OK)
async def get_users(
    db: AsyncSession = Depends(get_db),
) -> list[UserProfileResponse]:
    return await user_service.get_users(db=db)


@router.get("/wallets", include_in_schema=False, status_code=status.HTTP_200_OK)
async def get_user_wallets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> list[WalletSchema]:
    return await user_service.get_user_wallets(db=db)


@router.put("/profile", status_code=status.HTTP_202_ACCEPTED)
async def update_user_profile(
    profile_data: ProfileSchema,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileSchema:
    return await user_service.update_profile(
        profile_data=profile_data, db=db, current_user=current_user
    )


@router.get("/{user_id}/profile", status_code=status.HTTP_200_OK)
async def get_user_details(
    user_id:UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileSchema:
    return await user_service.get_user_with_profile(db=db, user_id=user_id)


@router.get("/restaurants", status_code=status.HTTP_200_OK)
async def get_restaurants(
    category_id: UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[VendorUserResponse]:
    """
    Get  all restaurant users, optionally filtered by category.
    """
    try:
        return await user_service.get_restaurant_vendors(db, category_id)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve food vendors: {str(e)}"
        )


@router.get(
    "/laundry-vendors",
    response_model=list[VendorUserResponse],
    status_code=status.HTTP_200_OK,
)
async def get_laundry_vendors(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Get users who provide laundry services.
    """
    return await user_service.get_users_by_laundry_services(db)


@router.post("/upload-image", status_code=status.HTTP_201_CREATED)
async def upload_profile_image(
    profile_image_url: UploadFile = File(None),
    backdrop_image_url: UploadFile = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProfileImageResponseSchema:
    return await user_service.upload_image_profile(
        db=db,
        current_user=current_user,
        backdrop_image_url=backdrop_image_url,
        profile_image_url=profile_image_url,
    )


@router.get("/riders", response_model=list[DispatchRiderSchema])
async def get_riders(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100)
):
    """Get all riders for a dispatch company"""
    if current_user.user_type != UserType.DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only dispatch companies can access this endpoint"
        )

    return await user_service.get_dispatcher_riders(db, current_user.id, skip, limit)


@router.get("/restaurants/{vendor_id}/reviews", status_code=status.HTTP_200_OK)
async def get_restaurant_reviews_endpoint(
    vendor_id: UUID,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
     current_user: User = Depends(get_current_user)
) -> list[CreateReviewSchema]:
    """
    Get all reviews for a specific restaurant.
    Used for the restaurant reviews page.
    """
    return await user_service.get_restaurant_reviews(db, vendor_id, limit, offset)


@router.get("/restaurants/{vendor_id}/menu", status_code=status.HTTP_200_OK)
async def get_restaurant_menu(
    vendor_id: UUID,
    db: AsyncSession = Depends(get_db),
     current_user: User = Depends(get_current_user)
)-> MenuWithReviewResponseSchema:
    """
    Get restaurant menu with individual item reviews.
    Used when customer visits a specific restaurant.
    """
    return await user_service.get_restaurant_menu_with_reviews(db, vendor_id)