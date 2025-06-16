from decimal import Decimal
from datetime import datetime
from app.schemas.item_schemas import ItemType
from app.models.models import Delivery, User, Item, Category, RefreshToken, Session
from sqlalchemy.orm import selectinload
from sqlalchemy import func, select, distinct, delete, update
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
from app.utils.s3_service import add_image, update_image, delete_s3_object
from app.config.config import redis_client
from app.models.models import User, Wallet, Profile, ProfileImage, Transaction, Review
from app.schemas.user_schemas import (
    Notification,
    ProfileSchema,
    UserProfileResponse,
    RiderProfileSchema,
    UserResponse,
    WalletSchema,
    VendorUserResponse,
    ProfileImageResponseSchema,
    RatingSchema,
    CreateReviewSchema,
    ProfileSchema,
    UpdateRider,
)

CACHE_TTL = 3600
logger = logging.getLogger(__name__)


def get_cached_user(user_id: UUID) -> dict:
    """Helper function to get cached user data"""
    cached_user = redis_client.get(f"user:{user_id}")
    return json.loads(cached_user) if cached_user else None


def set_cached_user(user_id: UUID, user_data: dict) -> None:
    """Helper function to set cached user data"""
    redis_client.set(
        f"user:{user_id}", json.dumps(user_data, default=str), ex=CACHE_TTL
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
        f"rider_id:{rider.id}", json.dumps(rider_dict, default=str), ex=CACHE_TTL
    )

    return rider_dict


async def get_current_user_details(db: AsyncSession, user_id: UUID) -> UserProfileResponse:
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

            "profile": {"phone_number": user.profile.phone_number,
                        "user_id": user.id,
                        "bike_number": getattr(user.profile, "bike_number", None),
                        "bank_account_number": getattr(
                            user.profile, "bank_account_number", None
                        ),
                        "bank_name": getattr(user.profile, "bank_name", None),
                        "full_name": user.profile.full_name,
                        "business_name": getattr(user.profile, "business_name", None),
                        "business_address": getattr(user.profile, "business_address", None),
                        "backdrop_image_url": getattr(user.profile.profile_image, 'backdrop_image_url', None),
                        "profile_image_url": getattr(user.profile.profile_image, 'profile_image_url', None),
                        "business_registration_number": getattr(
                            user.profile, "business_registration_number", None
                        ),
                        "closing_hours": user.profile.closing_hours.isoformat()
                        if getattr(user.profile, "closing_hours", None)
                        else None,
                        "opening_hours": user.profile.opening_hours.isoformat()
                        if getattr(user.profile, "opening_hours", None)
                        else None,}
        }

        # Cache the users data
        redis_client.set(f"current_useer_profile:{user_id}", json.dumps(user_profile_dict, default=str), ex=CACHE_TTL)

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
            redis_client.set("all_users", json.dumps([], default=str), ex=CACHE_TTL)
            return []

        # Convert to  response format
        users_data = []
        for user in users:
            user_data = {
                "email": user.email,
                "user_type": getattr(user, "user_type", "customer"),
                "id": str(user.id),
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
                    "business_name": getattr(user.profile, "business_name", None),
                    "business_address": getattr(user.profile, "business_address", None),
                    "backdrop_image_url": getattr(user.profile.profile_image, 'backdrop_image_url', None),
                    "profile_image_url": getattr(user.profile.profile_image, 'profile_image_url', None),
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
        redis_client.set("all_users", json.dumps(users_data, default=str), ex=CACHE_TTL)

        # Convert to response objects
        return [UserProfileResponse(**user_data) for user_data in users_data]

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve users: {str(e)}",
        )


async def get_user_wallets(db: AsyncSession) -> list[WalletSchema]:
    stmt = select(Wallet).options(selectinload(Wallet.transactions))
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_user_wallet(db: AsyncSession, current_user: User) -> WalletSchema:
    stmt = (
        select(Wallet)
        .where(Wallet.id == current_user.id)
        .options(selectinload(Wallet.transactions))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


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

    if (
        profile.phone_number
        or profile.business_name
        or profile.business_registration_number
    ) is not None:
        # Check if email is already taken by another user
        stmt = select(Profile).where(
            or_(
                Profile.phone_number == profile_data.phone_number,
                Profile.business_name == profile_data.business_name,
                Profile.business_registration_number
                == profile_data.business_registration_number,
            )
            & (Profile.user_id != current_user.id)
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number or business name or business registration number  already registered",
            )

        if profile:
            # Update profile fields
            for field, value in profile_data.model_dump(exclude_unset=True).items():
                setattr(profile, field, value)

        await db.commit()
        await db.refresh(profile)

        # Invalidate cached data
        invalidate_user_cache(current_user.id)
        redis_client.delete("all_users")

        return profile


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rider profile not found"
        )

    # Check for unique constraints if updating phone details
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
        result = await db.execute(select(Profile).options(selectinload(Profile.profile_image)).where(Profile.user_id == user_id))
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
            "business_name": profile.business_name or None,
            "business_address": profile.business_address or None,
            "business_registration_number": profile.business_registration_number
            or None,
            "closing_hours": profile.closing_hours or None,
            "opening_hours": profile.opening_hours or None,
            "profile_image_url": profile.profile_image.profile_image_url,
            "backdrop_image_url": profile.backdrop_image_url or None

        }

        # Cache the user data using your existing function
        set_cached_user(user_id, user_data)

        return ProfileSchema(**user_data)

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # logging.error(f"Error retrieving user with profile: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve user data: {str(e)}",
        )


# <<<<< --------- GET USER BY FOOD CATEGORY ---------- >>>>>


async def get_restaurant_vendors(
    db: AsyncSession, category_id: Optional[UUID] = None
) -> List[VendorUserResponse]:
    """
    Get restaurant vendors filtered by food category with their ratings.
    This is for the main restaurant listing page.
    """
    cache_key = f"restaurant_vendors:{category_id if category_id else 'all'}"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for {cache_key}")
        cached_vendors = json.loads(cached_data)
        # Convert cached data back to proper format
        result = []
        for vendor in cached_vendors:
            vendor["rating"] = RatingSchema(**vendor["rating"])
            result.append(VendorUserResponse(**vendor))
        return result

    try:
        # Subquery to calculate review statistics per vendor
        review_stats_subquery = (
            select(
                Item.user_id.label("vendor_id"),
                func.avg(Review.rating).label("avg_rating"),
                func.count(Review.id).label("review_count"),
            )
            .select_from(Item)
            .join(Review, Item.id == Review.item_id)
            .where(Item.item_type == ItemType.FOOD)
            .group_by(Item.user_id)
        ).subquery()

        # Main query to get vendors with their statistics
        stmt = (
            select(
                User,
                Profile,
                ProfileImage,
                func.count(distinct(Item.id)).label("item_count"),
                func.coalesce(review_stats_subquery.c.avg_rating, 0).label(
                    "avg_rating"
                ),
                func.coalesce(review_stats_subquery.c.review_count, 0).label(
                    "review_count"
                ),
            )
            .join(Profile, User.id == Profile.user_id)
            .outerjoin(ProfileImage, Profile.user_id == ProfileImage.profile_id)
            .join(Item, User.id == Item.user_id)
            .outerjoin(
                review_stats_subquery, User.id == review_stats_subquery.c.vendor_id
            )
            .where(
                Item.item_type == ItemType.FOOD,
                User.user_type == UserType.VENDOR.value,
            )
            .group_by(
                User.id,
                Profile.user_id,
                ProfileImage.profile_id,
                review_stats_subquery.c.avg_rating,
                review_stats_subquery.c.review_count,
            )
        )

        # Add category filter if provided
        if category_id:
            stmt = stmt.where(Item.category_id == category_id)

        # Execute query
        result = await db.execute(stmt)
        vendors = result.all()

        # Format response
        response = []
        for row in vendors:
            user, profile, profile_image, item_count, avg_rating, review_count = row

            if item_count > 0:  # Only include vendors with menu items
                # Create rating schema object
                rating_data = RatingSchema(
                    average_rating=Decimal(str(round(float(avg_rating), 2)))
                    if avg_rating
                    else Decimal("0.00"),
                    number_of_ratings=int(review_count) if review_count else 0,
                    reviews=[],  # Empty for listing page, populated separately when needed
                )

                vendor_data = VendorUserResponse(
                    id=str(user.id),
                    company_name=profile.business_name,
                    email=user.email,
                    phone_number=profile.phone_number,
                    profile_image=profile_image.profile_image_url
                    if profile_image
                    else None,
                    location=profile.business_address,
                    backdrop_image_url=profile_image.backdrop_image_url
                    if profile_image
                    else None,
                    opening_hour=profile.opening_hours.strftime("%H:%M")
                    if profile.opening_hours
                    else None,
                    closing_hour=profile.closing_hours.strftime("%H:%M")
                    if profile.closing_hours
                    else None,
                    rating=rating_data,
                    total_items=item_count,
                )
                response.append(vendor_data)

        # Cache the results
        serializable_response = []
        for vendor in response:
            vendor_dict = vendor.model_dump()
            vendor_dict["rating"] = {
                "average_rating": str(vendor.rating.average_rating)
                if vendor.rating.average_rating
                else "0.00",
                "number_of_ratings": vendor.rating.number_of_ratings,
                "reviews": [],
            }
            serializable_response.append(vendor_dict)

        redis_client.setex(
            cache_key, CACHE_TTL, json.dumps(serializable_response, default=str)
        )

        return response

    except Exception as e:
        logger.error(f"Error fetching restaurant vendors: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch restaurant vendors",
        )


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


async def get_users_by_laundry_services(
    db: AsyncSession, category_id: Optional[UUID] = None
) -> List[VendorUserResponse]:
    """
    Get restaurant vendors filtered by food category with their ratings.
    This is for the main restaurant listing page.
    """
    cache_key = f"laundry_vendors:{category_id if category_id else 'all'}"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for {cache_key}")
        cached_vendors = json.loads(cached_data)
        # Convert cached data back to proper format
        result = []
        for vendor in cached_vendors:
            vendor["rating"] = RatingSchema(**vendor["rating"])
            result.append(VendorUserResponse(**vendor))
        return result

    try:
        # Subquery to calculate review statistics per vendor
        review_stats_subquery = (
            select(
                Item.user_id.label("vendor_id"),
                func.avg(Review.rating).label("avg_rating"),
                func.count(Review.id).label("review_count"),
            )
            .select_from(Item)
            .join(Review, Item.id == Review.item_id)
            .where(Item.item_type == ItemType.LAUNDRY)
            .group_by(Item.user_id)
        ).subquery()

        # Main query to get vendors with their statistics
        stmt = (
            select(
                User,
                Profile,
                ProfileImage,
                func.count(distinct(Item.id)).label("item_count"),
                func.coalesce(review_stats_subquery.c.avg_rating, 0).label(
                    "avg_rating"
                ),
                func.coalesce(review_stats_subquery.c.review_count, 0).label(
                    "review_count"
                ),
            )
            .join(Profile, User.id == Profile.user_id)
            .outerjoin(ProfileImage, Profile.user_id == ProfileImage.profile_id)
            .join(Item, User.id == Item.user_id)
            .outerjoin(
                review_stats_subquery, User.id == review_stats_subquery.c.vendor_id
            )
            .where(
                Item.item_type == ItemType.LAUNDRY,
                User.user_type == UserType.VENDOR.value,
            )
            .group_by(
                User.id,
                Profile.user_id,
                ProfileImage.profile_id,
                review_stats_subquery.c.avg_rating,
                review_stats_subquery.c.review_count,
            )
        )

        # Add category filter if provided
        if category_id:
            stmt = stmt.where(Item.category_id == category_id)

        # Execute query
        result = await db.execute(stmt)
        vendors = result.all()

        # Format response
        response = []
        for row in vendors:
            user, profile, profile_image, item_count, avg_rating, review_count = row

            if item_count > 0:  # Only include vendors with menu items
                # Create rating schema object
                rating_data = RatingSchema(
                    average_rating=Decimal(str(round(float(avg_rating), 2)))
                    if avg_rating
                    else Decimal("0.00"),
                    number_of_ratings=int(review_count) if review_count else 0,
                    reviews=[],  # Empty for listing page, populated separately when needed
                )

                vendor_data = VendorUserResponse(
                    id=str(user.id),
                    company_name=profile.business_name,
                    email=user.email,
                    phone_number=profile.phone_number,
                    profile_image=profile_image.profile_image_url
                    if profile_image
                    else None,
                    location=profile.business_address,
                    backdrop_image_url=profile_image.backdrop_image_url
                    if profile_image
                    else None,
                    opening_hour=profile.opening_hours.strftime("%H:%M")
                    if profile.opening_hours
                    else None,
                    closing_hour=profile.closing_hours.strftime("%H:%M")
                    if profile.closing_hours
                    else None,
                    rating=rating_data,
                    total_items=item_count,
                )
                response.append(vendor_data)

        # Cache the results
        serializable_response = []
        for vendor in response:
            vendor_dict = vendor.model_dump()
            vendor_dict["rating"] = {
                "average_rating": str(vendor.rating.average_rating)
                if vendor.rating.average_rating
                else "0.00",
                "number_of_ratings": vendor.rating.number_of_ratings,
                "reviews": [],
            }
            serializable_response.append(vendor_dict)

        redis_client.setex(
            cache_key, CACHE_TTL, json.dumps(serializable_response, default=str)
        )

        return response

    except Exception as e:
        logger.error(f"Error fetching restaurant vendors: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch restaurant vendors",
        )


async def get_users_by_laundry_services1(db: AsyncSession) -> List[VendorUserResponse]:
    """
    Retrieve users who have items with item_type='laundry'.

    Args:
        db: The database session.

    Returns:
        List of dictionaries containing user details in the specified format.

    Raises:
        HTTPException: If an error occurs during the database query.
    """
    try:
        # Query to get users with laundry items
        stmt = (
            select(User)
            .join(Item, Item.user_id == User.id)
            .where(Item.item_type == ItemType.LAUNDRY)
            .options(
                selectinload(User.profile).selectinload(Profile.profile_image),
                # selectinload(User.profile).selectinload(Profile.profile_image),
            )
        )

        # Execute query
        result = await db.execute(stmt)
        users = result.scalars().unique().all()

        # Format the response
        response = []
        for user in users:
            response.append(
                {
                    "id": user.id,
                    "company_name": user.profile.business_name
                    if user.profile
                    else None,
                    "email": user.email,
                    "phone_number": user.profile.phone_number if user.profile else None,
                    "profile_image": user.profile.profile_image.url
                    if user.profile and user.profile.profile_image
                    else None,
                    "location": user.profile.business_address if user.profile else None,
                    "company_background_image": user.profile.backdrop.url
                    if user.profile.backdrop
                    else None,
                    "opening_hour": user.profile.opening_hours
                    if user.profile
                    else None,
                    "closing_hour": user.profile.closing_hours
                    if user.profile
                    else None,
                    "rating": await get_vendor_average_rating(user.id, db),
                }
            )

        return response

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve laundry service providers: {str(e)}",
        )


async def get_vendor_average_rating(user_id, db: AsyncSession) -> Decimal:
    stmt = (
        select(User)
        .join(Item, Item.user_id == user_id)
        .options(
            selectinload(User.items).selectinload(Item.reviews),
        )
    )

    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    ratings = []

    for item in user.items:
        for review in item.reviews:
            ratings.append(review.rating)

    average_rating = Decimal(sum(ratings) / len(ratings)) or 0.00

    return average_rating


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
        redis_client.setex(cache_key, CACHE_TTL, json.dumps(riders, default=str))
        return [DispatchRiderSchema(**rider) for rider in riders]

    except Exception as e:
        logger.error(f"Error fetching dispatcher riders: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch riders",
        )


async def delete_rider(rider_id: UUID, db: AsyncSession, current_user: User) -> None:
    """
    Delete rider using CASCADE DELETE (requires proper cascade setup in models).
    This is more efficient as it relies on database cascading.
    """

    if current_user.user_type != UserType.DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only dispatch users can delete riders",
        )

    try:
        stmt = select(User).where(
            User.id == rider_id, User.dispatcher_id == current_user.id
        )
        result = await db.execute(stmt)
        rider = result.scalar_one_or_none()

        if not rider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Rider not found"
            )

        if rider.user_type != UserType.RIDER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="User is not a rider"
            )

        await db.delete(rider)
        await db.commit()

        # Clear caches
        invalidate_user_cache(rider_id)
        invalidate_user_cache(current_user.id)
        redis_client.delete("all_users")

        logger.info(
            f"Rider {rider_id} deleted with cascade by dispatch user {current_user.id}"
        )

        return None

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error deleting rider {rider_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete rider",
        )


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

        # # Delete profile image
        # await db.execute(delete(ProfileImage).where(ProfileImage.profile_id == rider_id))

        # # Delete profile
        # await db.execute(delete(Profile).where(Profile.user_id == rider_id))

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
) -> Notification:


    cache_key = f"notification_token:{current_user.notification_token}"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        return Notification(**rider)

    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
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



async def get_current_user_notification_token(current_user: UUID, db: AsyncSession)-> Notification:

    result = await db.execute(select(User).where(User.id==current_user.id))

    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')

    if not user.notification_token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification token not found")

    return user.notification_token
