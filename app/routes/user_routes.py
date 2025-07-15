from uuid import UUID
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
    BackgroundTasks,
)


from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_current_user
from app.database.database import get_db
from app.models.models import User

from app.schemas.schemas import DispatchRiderSchema
from app.schemas.status_schema import UserType
from app.schemas.user_schemas import (
    Notification,
    ProfileSchema,
    UserProfileResponse,
    RiderProfileSchema,
    WalletSchema,
    VendorUserResponse,
    ProfileImageResponseSchema,
    CreateReviewSchema,
    UpdateRider,
)
from app.services import user_service
from app.schemas.item_schemas import MenuResponseSchema, FoodGroup


router = APIRouter(prefix="/api/users", tags=["Users"])


@router.put("/{user_id}/toggle-block", status_code=status.HTTP_200_OK)
async def toggle_user_block_status(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> bool:
    """
    Toggle user block status - block if unblocked, unblock if blocked.
    Only accessible to admin, staff, or moderator users.

    Returns:
        Boolean indicating the new block status (True if blocked, False if unblocked)
    """
    return await user_service.toggle_user_block_status(
        db=db, user_id=user_id, current_user=current_user
    )


@router.get("", status_code=status.HTTP_200_OK)
async def get_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[UserProfileResponse]:
    return await user_service.get_users(db=db, skip=skip, limit=limit)


@router.get("/wallets", include_in_schema=False, status_code=status.HTTP_200_OK)
async def get_user_wallets(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[WalletSchema]:
    return await user_service.get_user_wallets(db=db, skip=skip, limit=limit)


@router.get("/user-wallet", include_in_schema=False, status_code=status.HTTP_200_OK)
async def get_user_wallet(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> WalletSchema:
    return await user_service.get_user_wallet(db=db, current_user=current_user)


@router.put("/profile", status_code=status.HTTP_202_ACCEPTED)
async def update_user_profile(
    profile_data: ProfileSchema,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileSchema:
    return await user_service.update_profile(
        profile_data=profile_data, db=db, current_user=current_user
    )


@router.put("/{rider_id}/profile", status_code=status.HTTP_202_ACCEPTED)
async def update_rider_profile(
    rider_id: UUID,
    profile_data: UpdateRider,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UpdateRider:
    return await user_service.update_rider_profile(
        profile_data=profile_data, db=db, rider_id=rider_id, current_user=current_user
    )


@router.get("/{user_id}/profile", status_code=status.HTTP_200_OK)
async def get_user_details(
    user_id: UUID,
    # current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileSchema:
    return await user_service.get_user_with_profile(db=db, user_id=user_id)


@router.get("/{user_id}/current-user-profile", status_code=status.HTTP_200_OK)
async def get_current_user_details(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    return await user_service.get_current_user_details(db=db, user_id=user_id)


@router.get("/{user_id}/rider-profile", status_code=status.HTTP_200_OK)
async def get_rider_details(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RiderProfileSchema:
    return await user_service.get_rider_profile(db=db, user_id=user_id)


@router.get("/restaurants", status_code=status.HTTP_200_OK)
async def get_restaurants(
    category_id: UUID | None = None,
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
    db: AsyncSession = Depends(get_db)
) -> list[VendorUserResponse]:
    """
    Get users who provide laundry services.
    """
    return await user_service.get_users_by_laundry_services(db)


@router.put("/upload-image", status_code=status.HTTP_202_ACCEPTED)
async def upload_profile_image(
    background_task: BackgroundTasks,
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
        background_task=background_task,
    )


@router.get("/riders", response_model=list[DispatchRiderSchema])
async def get_riders(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
):
    """Get all riders for a dispatch company"""
    if current_user.user_type != UserType.DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only dispatch companies can access this endpoint",
        )

    return await user_service.get_dispatcher_riders(db, current_user.id, skip, limit)


@router.get("/restaurants/{vendor_id}/reviews", status_code=status.HTTP_200_OK)
async def get_restaurant_reviews_endpoint(
    vendor_id: UUID,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CreateReviewSchema]:
    """
    Get all reviews for a specific restaurant.
    Used for the restaurant reviews page.
    """
    return await user_service.get_restaurant_reviews(db, vendor_id, limit, offset)


@router.get("/restaurants/{restaurant_id}/menu", status_code=status.HTTP_200_OK)
async def get_restaurant_menu(
    restaurant_id: UUID,
    food_group: FoodGroup,
    db: AsyncSession = Depends(get_db),
) -> list[MenuResponseSchema]:
    """
    Get restaurant menu with individual item reviews.
    Used when customer visits a specific restaurant.
    """
    return await user_service.get_restaurant_menu(
        db=db, food_group=food_group, restaurant_id=restaurant_id
    )


@router.get("/laundry/{laundry_id}/menu", status_code=status.HTTP_200_OK)
async def get_laundry_menu(
    laundry_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[MenuResponseSchema]:
    """
    Get restaurant menu with individual item reviews.
    Used when customer visits a specific restaurant.
    """
    return await user_service.get_laundry_menu(db=db, laundry_id=laundry_id)


@router.get(
    "/teams", response_model=list[UserProfileResponse], status_code=status.HTTP_200_OK
)
async def get_teams_route(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[UserProfileResponse]:
    return await user_service.get_teams(db=db, skip=skip, limit=limit)


@router.get("/staff-list", status_code=status.HTTP_200_OK)
async def staff_list(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Only admin/superadmin should be able to access this
    if current_user.user_type not in [UserType.ADMIN, UserType.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
        )
    return await user_service.list_all_staff(db=db)


@router.delete("/{rider_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rider_endpoint(
    rider_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a rider, their profile, and profile image"""
    return await user_service.delete_rider(rider_id, db, current_user)


@router.put("/notification", status_code=status.HTTP_202_ACCEPTED)
async def register_for_push_notification(
    push_token: Notification,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add Push token"""
    return await user_service.register_notification(
        push_token=push_token, db=db, current_user=current_user
    )


@router.get("/notification", status_code=status.HTTP_200_OK)
async def get_push_notification(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Notification:
    """Delete Push Token"""
    return await user_service.get_current_user_notification_token(
        db=db, current_user=current_user
    )
