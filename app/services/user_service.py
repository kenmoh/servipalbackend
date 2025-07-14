from decimal import Decimal
from datetime import datetime
from sqlalchemy import cast
from sqlalchemy.dialects import postgresql
from app.schemas.item_schemas import FoodGroup, ItemType
from app.models.models import Delivery, User, Item, Category, RefreshToken, Session
from sqlalchemy.orm import selectinload
from sqlalchemy import func, select, distinct, delete, update, and_
from typing import List, Optional
from uuid import UUID
import json
import logging
from fastapi import HTTPException, status, UploadFile, BackgroundTasks

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload
from app.schemas.delivery_schemas import DeliveryStatus

from app.schemas.schemas import DispatchRiderSchema
from app.schemas.status_schema import AccountStatus, UserType
from app.utils.logger_config import setup_logger
from app.utils.s3_service import add_image, update_image, delete_s3_object
from app.config.config import redis_client, settings
from app.models.materialize_model import VendorReviewStats
from app.models.models import (
    User,
    Wallet,
    Profile,
    ProfileImage,
    Transaction,
    Review,
    Order,
    Item,
)
from app.schemas.review_schema import ReviewType
from app.schemas.item_schemas import MenuResponseSchema
from app.schemas.user_schemas import (
    Notification,
    ProfileSchema,
    TransactionSchema,
    UserProfileResponse,
    RiderProfileSchema,
    WalletSchema,
    VendorUserResponse,
    ProfileImageResponseSchema,
    RatingSchema,
    CreateReviewSchema,
    ProfileSchema,
    UpdateRider,
    WalletUserData,
)

logger = setup_logger()


def get_cached_user(user_id: UUID) -> dict:
    """Helper function to get cached user data"""
    cached_user = redis_client.get(f"user:{user_id}")
    return json.loads(cached_user) if cached_user else None


def set_cached_user(user_id: UUID, user_data: dict) -> None:
    """Helper function to set cached user data"""
    redis_client.set(
        f"user:{user_id}", json.dumps(user_data, default=str), ex=settings.REDIS_EX
    )


def invalidate_user_cache(user_id: UUID) -> None:
    """Helper function to invalidate user cache"""
    redis_client.delete(f"user:{user_id}")


async def get_rider_profile(db: AsyncSession, user_id: UUID) -> RiderProfileSchema:
    cached_user = redis_client.get(f"rider_id:{user_id}")
    if cached_user:
        users_data = json.loads(cached_user)
        return [UserProfileResponse(**user_data) for user_data in users_data]

    stmt = (
        select(User)
        .options(selectinload(User.profile).selectinload(Profile.profile_image))
        .where(User.id == user_id)
    )
    result = await db.execute(stmt)
    rider = result.scalar_one_or_none()

    rider_dict = {
        "profile_image_url": rider.profile.profile_image.profile_image_url,
        "full_name": rider.profile.full_name,
        "email": rider.email,
        "phone_number": rider.profile.phone_number,
        "business_address": rider.profile.business_address,
        "business_name": rider.profile.business_name,
        "bike_number": rider.profile.bike_number,
    }

    redis_client.set(
        f"rider_id:{rider.id}",
        json.dumps(rider_dict, default=str),
        ex=settings.REDIS_EX,
    )

    return rider_dict


async def get_current_user_details(
    db: AsyncSession, user_id: UUID
) -> UserProfileResponse:
    """
    Retrieves all users with their profiles.

    Args:
        db: Async database session.

    Returns:
        List of UserProfileResponse objects with user and profile data.
    """
    # Try to get from cache first
    cached_user = redis_client.get(f"current_useer_profile:{user_id}")
    if cached_user:
        user_data = json.loads(cached_user)
        return UserProfileResponse(**user_data)

    try:
        # Build optimized query to get users with profiles
        stmt = (
            select(User)
            .options(selectinload(User.profile).selectinload(Profile.profile_image))
            .where(User.id == user_id)
        )

        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        # Convert response format
        user_profile_dict = {
            "email": user.email,
            "user_type": user.user_type,
            "id": user.id,
            "profile": {
                "phone_number": user.profile.phone_number,
                "user_id": user.id,
                "bike_number": getattr(user.profile, "bike_number", None),
                "bank_account_number": getattr(
                    user.profile, "bank_account_number", None
                ),
                "bank_name": getattr(user.profile, "bank_name", None),
                "full_name": user.profile.full_name,
                "store_name": user.profile.store_name,
                "business_name": getattr(user.profile, "business_name", None),
                "business_address": getattr(user.profile, "business_address", None),
                "backdrop_image_url": getattr(
                    user.profile.profile_image, "backdrop_image_url", None
                ),
                "profile_image_url": getattr(
                    user.profile.profile_image, "profile_image_url", None
                ),
                "business_registration_number": getattr(
                    user.profile, "business_registration_number", None
                ),
                "closing_hours": user.profile.closing_hours.isoformat()
                if getattr(user.profile, "closing_hours", None)
                else None,
                "opening_hours": user.profile.opening_hours.isoformat()
                if getattr(user.profile, "opening_hours", None)
                else None,
            },
        }

        # Cache the users data
        redis_client.set(
            f"current_useer_profile:{user_id}",
            json.dumps(user_profile_dict, default=str),
            ex=settings.REDIS_EX,
        )

        # Convert to response objects
        return UserProfileResponse(**user_profile_dict)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve users: {str(e)}",
        )


async def get_users(db: AsyncSession) -> list[UserProfileResponse]:
    """
    Retrieves all users with their profiles.

    Args:
        db: Async database session.

    Returns:
        List of UserProfileResponse objects with user and profile data.
    """
    # Try to get from cache first
    cached_users = redis_client.get("all_users")
    if cached_users:
        users_data = json.loads(cached_users)
        return [UserProfileResponse(**user_data) for user_data in users_data]

    try:
        # Build optimized query to get users with profiles
        stmt = (
            select(User)
            .options(selectinload(User.profile).selectinload(Profile.profile_image))
            .order_by(User.created_at.desc())
        )

        result = await db.execute(stmt)
        users = result.scalars().all()

        if not users:
            # Cache empty result to avoid repeated DB queries
            redis_client.set(
                "all_users", json.dumps([], default=str), ex=settings.REDIS_EX
            )
            return []

        # Convert to  response format
        users_data = []
        for user in users:
            print(user.is_blocked, user.account_status)
            user_data = {
                "email": user.email,
                "user_type": getattr(user, "user_type", "customer"),
                "id": str(user.id),
                "is_blocked": user.is_blocked,
                "account_status": user.account_status
            }

            # Add profile if exists
            if user.profile:
                user_data["profile"] = {
                    "user_id": user.id,
                    "phone_number": user.profile.phone_number,
                    "bike_number": getattr(user.profile, "bike_number", None),
                    "bank_account_number": getattr(
                        user.profile, "bank_account_number", None
                    ),
                    "bank_name": getattr(user.profile, "bank_name", None),
                    "full_name": user.profile.full_name,
                    "store_name": user.profile.store_name,
                    "business_name": getattr(user.profile, "business_name", None),
                    "business_address": getattr(user.profile, "business_address", None),
                    "backdrop_image_url": getattr(
                        user.profile.profile_image, "backdrop_image_url", None
                    ),
                    "profile_image_url": getattr(
                        user.profile.profile_image, "profile_image_url", None
                    ),
                    "business_registration_number": getattr(
                        user.profile, "business_registration_number", None
                    ),
                    "closing_hours": user.profile.closing_hours.isoformat()
                    if getattr(user.profile, "closing_hours", None)
                    else None,
                    "opening_hours": user.profile.opening_hours.isoformat()
                    if getattr(user.profile, "opening_hours", None)
                    else None,
                }
            else:
                user_data["profile"] = None

            users_data.append(user_data)

        # Cache the users data
        redis_client.set(
            "all_users", json.dumps(users_data, default=str), ex=settings.REDIS_EX
        )

        # Convert to response objects
        return [UserProfileResponse(**user_data) for user_data in users_data]

    except Exception as e:
        logger.error(f"Failed to retrieve users: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve users: {str(e)}",
        )


async def toggle_user_block_status(
    db: AsyncSession, user_id: UUID, current_user: User
) -> bool:
    """
    Toggle user block status - block if unblocked, unblock if blocked.

    Args:
        db: Database session
        user_id: ID of the user to toggle block status
        current_user: Current authenticated user

    Returns:
        Boolean indicating the new block status
    """
    # Check if current user has permission to block/unblock users
    allowed_user_types = [UserType.ADMIN, UserType.SUPER_ADMIN,UserType.MODERATOR]
    if current_user.user_type not in allowed_user_types:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin, staff, or moderator users can block/unblock users",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Toggle the block status
    user.is_blocked = not user.is_blocked

    try:
        await db.commit()
        await db.refresh(user)

        # Invalidate user cache
        invalidate_user_cache(user_id)
        redis_client.delete("all_users")

        action = "blocked" if user.is_blocked else "unblocked"
        logger.info(
            f"User {user_id} ({user.email}) has been {action} by {current_user.email}"
        )

        return user.is_blocked

    except Exception as e:
        await db.rollback()
        logger.error(f"Error toggling block status for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user block status",
        )


# async def get_user_wallets(db: AsyncSession) -> list[WalletSchema]:
#     stmt = select(Wallet).options(selectinload(Wallet.transactions))
#     result = await db.execute(stmt)
#     wallets = result.scalars().all()
#     for wallet in wallets:
#         if hasattr(wallet, "transactions") and wallet.transactions:
#             wallet.transactions.sort(
#                 key=lambda t: getattr(t, "created_at", None), reverse=True
#             )
#     return wallets


async def get_user_wallets(db: AsyncSession) -> list[WalletSchema]:
    stmt = select(Wallet).options(
        selectinload(Wallet.transactions),
        selectinload(Wallet.user).selectinload(User.profile),
    )
    result = await db.execute(stmt)
    wallets = result.scalars().all()
    wallet_schemas = []
    for wallet in wallets:
        # Sort transactions by created_at descending
        if hasattr(wallet, "transactions") and wallet.transactions:
            wallet.transactions.sort(
                key=lambda t: getattr(t, "created_at", None), reverse=True
            )
        # Get user profile data
        profile = None
        if wallet.user and wallet.user.profile:
            profile = WalletUserData(
                full_name=wallet.user.profile.full_name,
                business_name=wallet.user.profile.business_name,
                phone_number=wallet.user.profile.phone_number,
            )
        wallet_schema = WalletSchema(
            id=wallet.id,
            balance=wallet.balance,
            escrow_balance=wallet.escrow_balance,
            profile=profile,
            transactions=[
                TransactionSchema.model_validate(tran) for tran in wallet.transactions
            ]
            if wallet.transactions
            else [],
        )
        wallet_schemas.append(wallet_schema)
    return wallet_schemas


async def get_user_wallet(db: AsyncSession, current_user: User) -> WalletSchema:
    stmt = (
        select(Wallet)
        .where(Wallet.id == current_user.id)
        .options(selectinload(Wallet.transactions))
    )
    result = await db.execute(stmt)
    wallet = result.scalar_one_or_none()
    if wallet and hasattr(wallet, "transactions") and wallet.transactions:
        wallet.transactions.sort(
            key=lambda t: getattr(t, "created_at", None), reverse=True
        )
    return wallet


async def update_profile(
    db: AsyncSession, profile_data: ProfileSchema, current_user: User
) -> ProfileSchema:
    """
    Args:
            db: Database session
            user_id: ID of the user to update
            user_data: Updated user data

    Returns:
            Updated user
    """
    # Get the user
    stmt = select(Profile).where(Profile.user_id == current_user.id)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Only check for conflicts if the new value is different from the current value
    conflict_filters = []
    if profile_data.phone_number and profile_data.phone_number != profile.phone_number:
        conflict_filters.append(Profile.phone_number == profile_data.phone_number)
    if (
        profile_data.business_name
        and profile_data.business_name != profile.business_name
    ):
        conflict_filters.append(Profile.business_name == profile_data.business_name)
    if (
        profile_data.business_registration_number
        and profile_data.business_registration_number
        != profile.business_registration_number
    ):
        conflict_filters.append(
            Profile.business_registration_number
            == profile_data.business_registration_number
        )

    if conflict_filters:
        stmt = select(Profile).where(
            or_(*conflict_filters), Profile.user_id != current_user.id
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number or business name or business registration number already registered",
            )

    # Update profile fields
    for field, value in profile_data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)

    # Invalidate cached data
    redis_client.delete(f"current_useer_profile:{current_user.id}")
    redis_client.get(f"user:{current_user.id}")
    redis_client.delete("all_users")

    return profile


async def list_all_staff(db: AsyncSession) -> list[User]:
    """
    List all users with user_type ADMIN, MODERATOR, or SUPER_ADMIN. Only admin/superadmin can access.
    """

    stmt = select(User).where(
        User.user_type.in_([UserType.ADMIN, UserType.MODERATOR, UserType.SUPER_ADMIN])
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def update_rider_profile(
    rider_id: UUID, db: AsyncSession, profile_data: UpdateRider, current_user: User
) -> UpdateRider:
    """
    Update a rider's profile. Only allows dispatch users to update riders they created.

    Args:
        rider_id: ID of the rider whose profile to update
        db: Database session
        profile_data: Updated profile data
        current_user: Currently authenticated dispatch user
    Returns:
        Updated rider profile
    """

    # Verify current user is a dispatch user
    if current_user.user_type != UserType.DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only dispatch users can update rider profiles",
        )

    # Get the rider and verify it was created by the current dispatch
    stmt = select(User).where(User.id == rider_id).options(selectinload(User.profile))
    result = await db.execute(stmt)
    rider = result.scalar_one_or_none()

    if not rider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rider not found"
        )

    # Verify the rider was created by the current dispatch user
    if rider.dispatcher_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update riders you created",
        )

    # Verify it's actually a rider user
    if rider.user_type != UserType.RIDER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User is not a rider"
        )

    profile = rider.profile
    if not profile:
        logger.error(f"Profile not found for rider {rider_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rider profile not found"
        )

    # Only check for conflicts if the new value is different from the current value
    conflict_filters = []
    if profile_data.phone_number and profile_data.phone_number != profile.phone_number:
        conflict_filters.append(Profile.phone_number == profile_data.phone_number)
    if profile_data.bike_number and profile_data.bike_number != profile.bike_number:
        conflict_filters.append(Profile.bike_number == profile_data.bike_number)

    if conflict_filters:
        stmt = select(Profile).where(
            or_(*conflict_filters), Profile.user_id != rider_id
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number or bike number already registered",
            )

    # Update profile fields
    profile.full_name = profile_data.full_name
    profile.phone_number = profile_data.phone_number
    profile.bike_number = profile_data.bike_number

    try:
        await db.commit()
        await db.refresh(profile)

        # Invalidate cached data
        invalidate_user_cache(rider_id)  # Cache for the rider being updated
        invalidate_user_cache(current_user.id)  # Cache for the dispatch user
        redis_client.delete("all_users")

        return profile

    except Exception as e:
        logger.error(f"Error updating rider profile: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update rider profile",
        )


async def get_user_with_profile(db: AsyncSession, user_id: UUID) -> ProfileSchema:
    """
    Retrieves a user with their profile, wallet, and recent transactions.

    Args:
        db: Async database session.
        current_user: The current authenticated user.

    Returns:
        UserResponse object with all related data.
    """

    # Try to get from cache first
    cached_user = get_cached_user(user_id)
    if cached_user:
        return ProfileSchema(**cached_user)

    try:
        result = await db.execute(
            select(Profile)
            .options(selectinload(Profile.profile_image))
            .where(Profile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
            )

        user_data = {
            "user_id": profile.user_id,
            "phone_number": profile.phone_number,
            "bike_number": profile.bike_number or None,
            "bank_account_number": profile.bank_account_number or None,
            "bank_name": profile.bank_name or None,
            "full_name": profile.full_name or None,
            "store_name": profile.store_name or None,
            "business_name": profile.business_name or None,
            "business_address": profile.business_address or None,
            "business_registration_number": profile.business_registration_number
            or None,
            "closing_hours": profile.closing_hours or None,
            "opening_hours": profile.opening_hours or None,
            "profile_image_url": profile.profile_image.profile_image_url or None,
            "backdrop_image_url": profile.profile_image.backdrop_image_url or None,
        }

        # Cache the user data using your existing function
        set_cached_user(user_id, user_data)

        return ProfileSchema(**user_data)

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logging.error(f"Error retrieving user with profile: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user data: {str(e)}",
        )


# <<<<< --------- GET USER BY FOOD CATEGORY ---------- >>>>>


async def get_restaurant_vendors(
    db: AsyncSession, category_id: UUID | None = None
) -> list[VendorUserResponse]:
    cache_key = f"restaurant_vendors:{category_id if category_id else 'all'}"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        return json.loads(cached_data)

    try:
        # Review stats subquery (for ORDER type reviews)
        review_stats_subq = (
            select(
                Order.vendor_id.label("vendor_id"),
                func.avg(Review.rating).label("average_rating"),
                func.count(Review.id).label("review_count"),
            )
            .join(Review, Review.order_id == Order.id)
            .where(Review.review_type == ReviewType.ORDER)
            .group_by(Order.vendor_id)
            .subquery()
        )

        # Main query
        stmt = (
            select(
                User,
                Profile,
                ProfileImage,
                func.coalesce(review_stats_subq.c.average_rating, 0).label(
                    "avg_rating"
                ),
                func.coalesce(review_stats_subq.c.review_count, 0).label(
                    "review_count"
                ),
            )
            .join(Profile, Profile.user_id == User.id)
            .outerjoin(ProfileImage, ProfileImage.profile_id == User.id)
            .outerjoin(review_stats_subq, review_stats_subq.c.vendor_id == User.id)
            .where(User.user_type == UserType.RESTAURANT_VENDOR)
        )

        # If category_id is provided, filter vendors to only those with items in that category
        if category_id:
            vendors_with_category_items = (
                select(Item.user_id)
                .where(
                    and_(
                        Item.item_type == ItemType.FOOD, Item.category_id == category_id
                    )
                )
                .distinct()
                .subquery()
            )
            stmt = stmt.join(
                vendors_with_category_items,
                vendors_with_category_items.c.user_id == User.id,
            )

        result = await db.execute(stmt)
        rows = result.all()
        response = []
        for user, profile, image, avg_rating, review_count in rows:
            vendor_dict = {
                "id": str(user.id),
                "company_name": profile.business_name or "",
                "email": user.email,
                "phone_number": profile.phone_number,
                "profile_image": image.profile_image_url if image else None,
                "location": profile.business_address,
                "backdrop_image_url": image.backdrop_image_url if image else None,
                "opening_hour": (
                    profile.opening_hours.strftime("%H:%M:%S")
                    if profile.opening_hours
                    else None
                ),
                "closing_hour": (
                    profile.closing_hours.strftime("%H:%M:%S")
                    if profile.closing_hours
                    else None
                ),
                "rating": {
                    "average_rating": str(round(float(avg_rating or 0), 2)),
                    "number_of_reviews": review_count or 0,
                },
            }
            response.append(vendor_dict)

        # Cache result
        redis_client.setex(
            cache_key, settings.REDIS_EX, json.dumps(response, default=str)
        )
        return response
    except Exception as e:
        logger.error(f"Error fetching vendors: {str(e)}")
        raise


async def get_vendor_reviews(
    db: AsyncSession, vendor_id: UUID, limit: int = 20, offset: int = 0
) -> List[CreateReviewSchema]:
    """
    Get all reviews for a specific restaurant (from all their menu items).
    This is for the restaurant reviews page.
    """
    try:
        reviews_query = (
            select(
                Review,
                Item.name.label("item_name"),
                User.email.label(
                    "reviewer_email"
                ),  # or first_name, last_name if available
                User.id.label("reviewer_id"),
            )
            .join(Item, Review.item_id == Item.id)
            .join(User, Review.user_id == User.id)  # Join with reviewer
            .where(Item.user_id == vendor_id, Item.item_type == ItemType.FOOD)
            .order_by(Review.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        result = await db.execute(reviews_query)
        reviews_data = result.all()

        # Convert to schema
        reviews = []
        for review, item_name, reviewer_email, reviewer_id in reviews_data:
            reviews.append(
                CreateReviewSchema(
                    id=str(review.id),
                    rating=review.rating,
                    comment=review.comment,
                    created_at=review.created_at,
                    reviewer_name=reviewer_email,  # You might want to use actual name if available
                    item_name=item_name,
                )
            )

        return reviews

    except Exception as e:
        logger.error(
            f"Error fetching restaurant reviews for vendor {vendor_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch restaurant reviews",
        )


async def upload_image_profile(
    current_user: User,
    profile_image_url: Optional[UploadFile],
    backdrop_image_url: Optional[UploadFile],
    background_task: BackgroundTasks,
    db: AsyncSession,
) -> ProfileImageResponseSchema:
    """
    Enhanced version with better logging and error handling
    """
    logger.info(f"Starting image upload for user {current_user.id}")

    try:
        # Get profile with profile_image relationship loaded
        result = await db.execute(
            select(Profile)
            .where(Profile.user_id == current_user.id)
            .options(selectinload(Profile.profile_image))
        )
        profile = result.scalar_one_or_none()

        if not profile:
            logger.error(f"Profile not found for user {current_user.id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="User profile not found!"
            )

        logger.info(
            f"Profile found. Existing profile_image: {profile.profile_image is not None}"
        )

        # Process image uploads
        new_profile_image_url = None
        new_backdrop_image_url = None
        old_profile_url = None
        old_backdrop_url = None

        # Store old URLs for cleanup
        if profile.profile_image:
            old_profile_url = profile.profile_image.profile_image_url
            old_backdrop_url = profile.profile_image.backdrop_image_url
            logger.info(
                f"Existing URLs - Profile: {old_profile_url}, Backdrop: {old_backdrop_url}"
            )

        if profile_image_url:
            logger.info("Uploading new profile image")
            new_profile_image_url = await add_image(profile_image_url)
            if not new_profile_image_url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload profile image",
                )
            logger.info(f"New profile image uploaded: {new_profile_image_url}")

        if backdrop_image_url:
            logger.info("Uploading new backdrop image")
            new_backdrop_image_url = await add_image(backdrop_image_url)
            if not new_backdrop_image_url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload backdrop image",
                )
            logger.info(f"New backdrop image uploaded: {new_backdrop_image_url}")

        # Handle profile image creation/update
        if profile.profile_image:
            logger.info("Updating existing profile image record")

            # Update profile image URL if new one provided
            if new_profile_image_url:
                profile.profile_image.profile_image_url = new_profile_image_url
                logger.info("Updated profile_image_url")

            # Update backdrop image URL if new one provided
            if new_backdrop_image_url:
                profile.profile_image.backdrop_image_url = new_backdrop_image_url
                logger.info("Updated backdrop_image_url")

            # Update timestamp
            profile.profile_image.updated_at = datetime.now()

        else:
            logger.info("Creating new profile image record")
            profile_image = ProfileImage(
                profile_id=profile.user_id,
                profile_image_url=new_profile_image_url,
                backdrop_image_url=new_backdrop_image_url,
            )
            db.add(profile_image)
            await db.flush()  # Flush to get the ID
            profile.profile_image = profile_image
            logger.info(
                f"Created new ProfileImage with profile_id: {profile_image.profile_id}"
            )

        # Commit the transaction
        logger.info("Committing transaction")
        await db.commit()
        logger.info("Transaction committed successfully")

        # Clean up old images AFTER successful commit
        if (
            old_profile_url
            and new_profile_image_url
            and old_profile_url != new_profile_image_url
        ):
            logger.info(f"Scheduling deletion of old profile image: {old_profile_url}")
            background_task.add_task(delete_s3_object, old_profile_url)

        if (
            old_backdrop_url
            and new_backdrop_image_url
            and old_backdrop_url != new_backdrop_image_url
        ):
            logger.info(
                f"Scheduling deletion of old backdrop image: {old_backdrop_url}"
            )
            background_task.add_task(delete_s3_object, old_backdrop_url)

        # Refresh to get the latest data
        await db.refresh(profile)
        if profile.profile_image:
            await db.refresh(profile.profile_image)

        # Verify the data was saved
        final_profile_url = (
            profile.profile_image.profile_image_url if profile.profile_image else None
        )
        final_backdrop_url = (
            profile.profile_image.backdrop_image_url if profile.profile_image else None
        )

        logger.info(
            f"Final URLs - Profile: {final_profile_url}, Backdrop: {final_backdrop_url}"
        )

        # Return the response
        if profile.profile_image:
            return ProfileImageResponseSchema(
                profile_image_url=profile.profile_image.profile_image_url,
                backdrop_image_url=profile.profile_image.backdrop_image_url,
            )
        else:
            logger.error("Failed to create/update profile image")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create/update profile image",
            )

    except HTTPException:
        # Re-raise HTTP exceptions
        await db.rollback()
        raise
    except Exception as e:
        # Handle unexpected errors
        await db.rollback()
        logger.error(f"Error in upload_image_profile: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Something went wrong: {str(e)}",
        )


# <<<<< --------- GET LAUNDRY SERVICE PROVIDERS ---------- >>>>>


async def get_users_by_laundry_services(db: AsyncSession) -> list[VendorUserResponse]:
    """
    Fetch all vendors who offer laundry services with:
    - basic info
    - number of laundry items
    - avg review (from Review via Order)
    - review count
    """
    cache_key = f"laundry_vendors"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for {cache_key}")
        return json.loads(cached_data)

    try:
        # Review stats subquery (ORDER review type only)
        review_stats_subq = (
            select(
                Order.vendor_id.label("vendor_id"),
                func.avg(Review.rating).label("average_rating"),
                func.count(Review.id).label("review_count"),
            )
            .join(Review, Review.order_id == Order.id)
            .where(Review.review_type == ReviewType.ORDER)
            .group_by(Order.vendor_id)
            .subquery()
        )

        # Laundry item count subquery
        item_count_subq = select(
            Item.user_id.label("vendor_id"),
            func.count(Item.id).label("total_items"),
        ).where(Item.item_type == ItemType.LAUNDRY)

        item_count_subq = item_count_subq.group_by(Item.user_id).subquery()

        # Main query
        stmt = (
            select(
                User,
                Profile,
                ProfileImage,
                func.coalesce(review_stats_subq.c.average_rating, 0).label(
                    "avg_rating"
                ),
                func.coalesce(review_stats_subq.c.review_count, 0).label(
                    "review_count"
                ),
                func.coalesce(item_count_subq.c.total_items, 0).label("total_items"),
            )
            .join(Profile, Profile.user_id == User.id)
            .outerjoin(ProfileImage, ProfileImage.profile_id == User.id)
            .outerjoin(review_stats_subq, review_stats_subq.c.vendor_id == User.id)
            .outerjoin(item_count_subq, item_count_subq.c.vendor_id == User.id)
            .where(User.user_type == UserType.LAUNDRY_VENDOR)
        )

        result = await db.execute(stmt)
        rows = result.all()

        response = []
        for user, profile, image, avg_rating, review_count, total_items in rows:
            if total_items == 0:
                continue  # skip vendors with no laundry services

            vendor_dict = {
                "id": str(user.id),
                "company_name": profile.business_name or "",
                "email": user.email,
                "phone_number": profile.phone_number,
                "profile_image": image.profile_image_url if image else None,
                "location": profile.business_address,
                "backdrop_image_url": image.backdrop_image_url if image else None,
                "opening_hour": (
                    profile.opening_hours.strftime("%H:%M:%S")
                    if profile.opening_hours
                    else None
                ),
                "closing_hour": (
                    profile.closing_hours.strftime("%H:%M:%S")
                    if profile.closing_hours
                    else None
                ),
                "rating": {
                    "average_rating": str(round(float(avg_rating or 0), 2)),
                    "number_of_reviews": review_count or 0,
                },
                "total_items": total_items,
            }

            response.append(vendor_dict)

        # Cache result
        redis_client.setex(
            cache_key,
            settings.REDIS_EX,
            json.dumps(response, default=str),
        )

        return response

    except Exception as e:
        logger.error(f"Error fetching laundry vendors: {str(e)}")
        raise


async def get_dispatcher_riders(
    db: AsyncSession, dispatcher_id: UUID, skip: int = 0, limit: int = 10
) -> list[DispatchRiderSchema]:
    """Get all riders for a dispatch company with their stats"""
    # Try to get from cache first
    cache_key = f"dispatcher:{dispatcher_id}:riders:{skip}:{limit}"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for {cache_key}")
        return [DispatchRiderSchema(**rider) for rider in json.loads(cached_data)]

    try:
        # Get riders with their profiles, profile images and delivery counts
        query = (
            select(
                User,
                Profile,
                ProfileImage.profile_image_url,
                func.count(Delivery.id)
                .filter(Delivery.delivery_status != DeliveryStatus.RECEIVED)
                .label("pending_deliveries"),
                func.count(Delivery.id).label("total_deliveries"),
                func.count(Delivery.id)
                .filter(Delivery.delivery_status == DeliveryStatus.RECEIVED)
                .label("completed_deliveries"),
            )
            .select_from(User)
            .join(Profile, Profile.user_id == User.id)
            .outerjoin(ProfileImage, ProfileImage.profile_id == Profile.user_id)
            .outerjoin(Delivery, Delivery.rider_id == User.id)
            .filter(
                User.dispatcher_id == dispatcher_id,
                User.user_type == UserType.RIDER,
            )
            .group_by(User.id, Profile.user_id, ProfileImage.profile_image_url)
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(query)
        riders_data = result.all()

        # Format response
        riders = []
        for user, profile, profile_image_url, pending, total, completed in riders_data:
            rider_data = {
                "id": str(user.id),
                "email": user.email,
                "full_name": profile.full_name,
                "phone_number": profile.phone_number,
                "bike_number": profile.bike_number,
                "profile_image_url": profile_image_url,
                "created_at": user.created_at,
                "stats": {
                    "total_deliveries": total,
                    "pending_deliveries": pending,
                    "completed_deliveries": completed,
                },
            }
            riders.append(rider_data)

        # Cache the results
        redis_client.setex(
            cache_key, settings.REDIS_EX, json.dumps(riders, default=str)
        )
        return [DispatchRiderSchema(**rider) for rider in riders]

    except Exception as e:
        logger.error(f"Error fetching dispatcher riders: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch riders",
        )


# async def delete_rider(rider_id: UUID, db: AsyncSession, current_user: User) -> None:
#     """
#     Delete rider using CASCADE DELETE (requires proper cascade setup in models).
#     This is more efficient as it relies on database cascading.
#     """

#     if current_user.user_type != UserType.DISPATCH:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Only dispatch users can delete riders",
#         )

#     try:
#         stmt = select(User).where(
#             User.id == rider_id, User.dispatcher_id == current_user.id
#         )
#         result = await db.execute(stmt)
#         rider = result.scalar_one_or_none()

#         if not rider:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND, detail="Rider not found"
#             )

#         if rider.user_type != UserType.RIDER:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST, detail="User is not a rider"
#             )

#         await db.delete(rider)
#         await db.commit()

#         # Clear caches
#         invalidate_user_cache(rider_id)
#         invalidate_user_cache(current_user.id)
#         redis_client.delete("all_users")

#         logger.info(
#             f"Rider {rider_id} deleted with cascade by dispatch user {current_user.id}"
#         )

#         return None

#     except HTTPException:
#         raise
#     except Exception as e:
#         await db.rollback()
#         logger.error(f"Error deleting rider {rider_id}: {str(e)}")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Failed to delete rider",
#         )


async def delete_rider(rider_id: UUID, db: AsyncSession, current_user: User) -> None:
    """
    Delete method that explicitly handles related records.
    """

    if current_user.user_type != UserType.DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only dispatch users can delete riders",
        )

    try:
        # Get the rider
        stmt = select(User).where(
            User.id == rider_id,
            User.dispatcher_id == current_user.id,
            User.user_type == UserType.RIDER,
        )
        result = await db.execute(stmt)
        rider = result.scalar_one_or_none()

        if not rider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rider not found or you don't have permission to delete this rider",
            )

        rider_email = rider.email

        # Manually delete related records

        # Delete refresh tokens
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id == rider_id))

        # Delete Sessions
        await db.execute(delete(Session).where(Session.user_id == rider_id))

        # Handle deliveries - you might want to set status to "cancelled" instead of deleting
        await db.execute(
            update(Delivery).where(Delivery.rider_id == rider_id).values(rider_id=None)
        )

        # Finally delete the user
        await db.delete(rider)
        await db.commit()

        # Clear caches
        try:
            invalidate_user_cache(rider_id)
            invalidate_user_cache(current_user.id)
            redis_client.delete("all_users")
        except Exception as cache_error:
            logger.warning(f"Cache invalidation failed: {str(cache_error)}")

        logger.info(
            f"Rider {rider_id} ({rider_email}) deleted by dispatcher {current_user.id}"
        )

        return None

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error deleting rider {rider_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete rider. Please try again.",
        )


async def register_notification(
    push_token: Notification,
    db: AsyncSession,
    current_user: User,
) -> str:
    cache_key = f"notification_token:{current_user.notification_token}"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        return cached_data

    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Check if token already exists and is the same
    if user.notification_token == push_token.notification_token:
        # Token already exists and is the same, just return it
        return user.notification_token

    # Update the notification token (either add new or update existing)
    await db.execute(
        update(User)
        .where(User.id == current_user.id)
        .values({"notification_token": push_token.notification_token})
    )

    await db.commit()

    # Cache the new token
    redis_client.setex(cache_key, 3600, push_token.notification_token)

    return user.notification_token


async def get_current_user_notification_token(
    current_user: UUID, db: AsyncSession
) -> Notification:
    result = await db.execute(select(User).where(User.id == current_user.id))

    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if not user.notification_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification token not found"
        )

    return {"notification_token": user.notification_token}


async def get_restaurant_menu(
    db: AsyncSession, restaurant_id: UUID, food_group: FoodGroup = FoodGroup.MAIN_COURSE
) -> list[MenuResponseSchema]:
    """
    Retrieve all menu items for a specific restaurant with Redis caching.

    Args:
        db: Async database session
        restaurant_id: UUID of the restaurant


    Returns:
        List of MenuResponseSchema objects

    Raises:
        SQLAlchemyError: If database query fails
    """
    cache_key = f"restaurant_menu:{restaurant_id}:{food_group}"

    # Check cache first
    cached_data = redis_client.get(cache_key)
    if cached_data:
        menu_data = json.loads(cached_data)
        # Convert back to Pydantic models
        return [MenuResponseSchema(**item) for item in menu_data]
    try:
        # Query menu items with eager loading of images
        menu_stmt = (
            select(Item)
            .where(
                Item.user_id == restaurant_id,
                Item.item_type == ItemType.FOOD,
                Item.food_group == food_group,
            )
            .options(selectinload(Item.images))
        )
        result = await db.execute(menu_stmt)
        menus = result.unique().scalars().all()

        # Transform each menu item to response schema
        menu_list = []
        for menu in menus:
            menu_dict = {
                "id": menu.id,
                "user_id": menu.user_id,
                "name": menu.name,
                "item_type": menu.item_type,
                "description": menu.description,
                "price": menu.price,
                "food_group": menu.food_group,
                "images": [
                    {"id": image.id, "item_id": image.item_id, "url": image.url}
                    for image in menu.images
                ],
            }
            menu_list.append(menu_dict)

        # Cache the data
        redis_client.setex(
            cache_key, settings.REDIS_EX, json.dumps(menu_list, default=str)
        )

        # Convert to Pydantic models for return
        return [MenuResponseSchema(**item) for item in menu_list]

    except Exception as e:
        # Log the error (you might want to use proper logging here)
        print(f"Error fetching restaurant menu: {e}")
        raise


async def get_laundry_menu(
    db: AsyncSession, laundry_id: UUID
) -> list[MenuResponseSchema]:
    """
    Retrieve all menu items for a specific restaurant with Redis caching.

    Args:
        db: Async database session
        laundry_id: UUID of the laundry vendorr


    Returns:
        List of MenuResponseSchema objects

    Raises:
        SQLAlchemyError: If database query fails
    """
    cache_key = f"laundry_menu:{laundry_id}"

    # Check cache first
    cached_data = redis_client.get(cache_key)
    if cached_data:
        menu_data = json.loads(cached_data)
        # Convert back to Pydantic models
        return [MenuResponseSchema(**item) for item in menu_data]
    try:
        # Query menu items with eager loading of images
        menu_stmt = (
            select(Item)
            .where(Item.user_id == laundry_id, Item.item_type == ItemType.LAUNDRY)
            .options(selectinload(Item.images))
        )
        result = await db.execute(menu_stmt)
        menus = result.unique().scalars().all()

        # Transform each menu item to response schema
        menu_list = []
        for menu in menus:
            menu_dict = {
                "id": menu.id,
                "user_id": menu.user_id,
                "name": menu.name,
                "item_type": menu.item_type,
                "price": menu.price,
                "images": [
                    {"id": image.id, "item_id": image.item_id, "url": image.url}
                    for image in menu.images
                ],
            }
            menu_list.append(menu_dict)

        # Cache the data
        redis_client.setex(
            cache_key, settings.REDIS_EX, json.dumps(menu_list, default=str)
        )

        # Convert to Pydantic models for return
        return [MenuResponseSchema(**item) for item in menu_list]

    except Exception as e:
        logger.error(f"Error fetching laundry menu: {e}")
        raise


async def get_teams(db: AsyncSession) -> list[UserProfileResponse]:
    """
    Returns all users with user_type ADMIN, SUPER_ADMIN, or MODERATOR.
    """
   

    # Try to get from cache first
    cached_users = redis_client.get("teams")
    if cached_users:
        users_data = json.loads(cached_users)
        return [UserProfileResponse(**user_data) for user_data in users_data]

    try:
        # Build optimized query to get users with profiles
        stmt = (
        select(User)
            .options(selectinload(User.profile).selectinload(Profile.profile_image))
            .where(
                User.user_type.in_(
                    [UserType.ADMIN, UserType.SUPER_ADMIN, UserType.MODERATOR]
                    )
                )
                .order_by(User.created_at.desc())
        )
        

        result = await db.execute(stmt)
        users = result.scalars().all()

        if not users:
            # Cache empty result to avoid repeated DB queries
            redis_client.set(
                "teams", json.dumps([], default=str), ex=settings.REDIS_EX
            )
            return []

        # Convert to  response format
        users_data = []
        for user in users:
            user_data = {
                "email": user.email,
                "user_type": user.user_type,
                "id": str(user.id),
                "is_blocked": user.is_blocked,
                "account_status": user.account_status
            }

            # Add profile if exists
            if user.profile:
                user_data["profile"] = {
                    "user_id": user.id,
                    "phone_number": user.profile.phone_number,
                    "bike_number": getattr(user.profile, "bike_number", None),
                    "bank_account_number": getattr(
                        user.profile, "bank_account_number", None
                    ),
                    "bank_name": getattr(user.profile, "bank_name", None),
                    "full_name": user.profile.full_name,
                    "store_name": user.profile.store_name,
                    "business_name": getattr(user.profile, "business_name", None),
                    "business_address": getattr(user.profile, "business_address", None),
                    "backdrop_image_url": getattr(
                        user.profile.profile_image, "backdrop_image_url", None
                    ),
                    "profile_image_url": getattr(
                        user.profile.profile_image, "profile_image_url", None
                    ),
                    "business_registration_number": getattr(
                        user.profile, "business_registration_number", None
                    ),
                    "closing_hours": user.profile.closing_hours.isoformat()
                    if getattr(user.profile, "closing_hours", None)
                    else None,
                    "opening_hours": user.profile.opening_hours.isoformat()
                    if getattr(user.profile, "opening_hours", None)
                    else None,
                }
            else:
                user_data["profile"] = None

            users_data.append(user_data)

        # Cache the users data
        redis_client.set(
            "teams", json.dumps(users_data, default=str), ex=settings.REDIS_EX
        )

        # Convert to response objects
        return [UserProfileResponse(**user_data) for user_data in users_data]

    except Exception as e:
        logger.error(f"Failed to retrieve users: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve users: {str(e)}",
        )