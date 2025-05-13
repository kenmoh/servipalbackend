from decimal import Decimal
from app.schemas.item_schemas import ItemType
from app.models.models import User, Item, Category
from sqlalchemy.orm import selectinload
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID
import json
from fastapi import HTTPException, status, UploadFile

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload, joinedload

from app.utils.s3_service import add_profile_image, update_image
from app.config.config import redis_client
from app.models.models import User, Wallet, Profile, ProfileImage
from app.schemas.user_schemas import (
    ProfileSchema,
    UserProfileResponse,
    UserResponse,
    WalletSchema,
    VendorUserResponse,
    ProfileImageResponseSchema,
)

CACHE_TTL = 3600


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


async def get_users(db: AsyncSession) -> list[UserResponse]:
    cached_users = redis_client.get("all_users")
    if cached_users:
        return json.loads(cached_users)

    stmt = select(User).options(
        selectinload(User.profile),
        selectinload(User.wallet),
        selectinload(User.wallet, Wallet.transactions),
    )
    result = await db.execute(stmt)
    users = result.scalars().all()

    # Cache the users
    redis_client.set("all_users", json.dumps(users, default=str), ex=CACHE_TTL)

    return users


# async def get_user_wallets(db: AsyncSession) -> list[WalletRespose]:
#     stmt = select(Wallet)
#     result = await db.execute(stmt)
#     return result.scalars().all()


async def get_user_wallets(db: AsyncSession) -> list[WalletSchema]:
    stmt = select(Wallet).options(selectinload(Wallet.transactions))
    result = await db.execute(stmt)
    return result.scalars().all()


async def create_pofile(
    db: AsyncSession, current_user: User, profile_data: ProfileSchema
) -> ProfileSchema:
    """
    Create a new user profile in the database.

    Args:
        db: Database session
        user_id: User ID from request
        profile_data: Profile data from request

    Returns:
        The newly created user profile
    """

    # Update the user's profile
    profile = Profile(
        user_id=current_user.id,
        **profile_data.model_dump(exclude_unset=True),
        # phone_number=profile_data.phone_number,
        # bank_account_number=profile_data.bank_account_number,
        # bank_name=profile_data.bank_name,
        # full_name=profile_data.full_name,
        # business_name=profile_data.business_name,
        # business_address=profile_data.business_address,
        # business_registration_number=profile_data.business_registration_number,
        # closing_hours=profile_data.closing_hours,
        # opening_hours=profile_data.opening_hours,
    )

    # Add user to database
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    invalidate_user_cache(current_user.id)
    redis_client.delete("all_users")

    return profile


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

        # profile.phone_number = profile_data.phone_number
        # profile.business_name = profile_data.business_name
        # profile.business_registration_number = profile_data.business_registration_number
        # profile.business_address = profile_data.business_address
        # profile.closing_hours = profile_data.closing_hours
        # profile.opening_hours = profile_data.opening_hours
        # profile.full_name = profile_data.full_name

        if profile:
            # Update profile fields
            for field, value in profile_data.model_dump(exclude_unset=True).items():
                setattr(profile, field, value)

        await db.commit()
        await db.refresh(profile)

        # Invalidate cached data
        await invalidate_user_cache(current_user.id)
        await redis_client.delete("all_users")

        return profile


async def get_user_with_profile(db: AsyncSession, user_id: UUID) -> UserProfileResponse:
    """
    Retrieves a user and their associated profile by user ID.

    Args:
        db: Async database session.
        user_id: ID of the user to retrieve.

    Returns:
        A User object with the profile loaded, or None if not found.
    """
    cached_user = await get_cached_user(user_id)
    if cached_user:
        return UserProfileResponse(**cached_user)

    query = (
        select(User)
        .options(joinedload(User.profile))  # Eagerly load the profile
        .where(User.id == user_id)
    )

    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user:
        # Cache the user data
        await set_cached_user(user_id, user.dict())
    return user


# <<<<< --------- GET USER BY FOOD CATEGORY ---------- >>>>>


async def get_users_by_food_category(
    db: AsyncSession, category_id: Optional[UUID] = None
) -> List[VendorUserResponse]:
    """
    Retrieve users who have items with item_type='food' in a specific category.
    If no users are found for the category, return all users with food items.
    If category_id is None, return all users with food items.

    Args:
        db: The database session.
        category_id: Optional UUID of the category to filter by.

    Returns:
        List of dictionaries containing user details.
    """
    # Base query to get users with food items
    stmt = (
        select(User)
        .join(Item, Item.user_id == User.id)
        .where(Item.item_type == ItemType.FOOD)
        .options(
            selectinload(User.profile).selectinload(Profile.profile_image),
            selectinload(User.profile).selectinload(Profile.backdrop),
        )
    )

    # Add category filter if provided
    if category_id:
        stmt = stmt.where(Item.category_id == category_id)

    # Execute query
    result = await db.execute(stmt)
    users = result.scalars().unique().all()

    # If no users found for the specific category, fall back to all food items
    if not users and category_id:
        stmt = (
            select(User)
            .join(Item, Item.user_id == User.id)
            .where(Item.item_type == ItemType.FOOD.value)
            .options(
                selectinload(User.profile).selectinload(Profile.profile_image),
                selectinload(User.profile).selectinload(Profile.backdrop),
            )
        )
        result = await db.execute(stmt)
        users = result.scalars().unique().all()

    # Format the response
    response = []
    for user in users:
        response.append(
            {
                "id": user.id,
                "company_name": user.profile.business_name if user.profile else None,
                "email": user.email,
                "phone_number": user.profile.phone_number if user.profile else None,
                "profile_image": user.profile.profile_image_url
                if user.profile and user.profile.profile_image_url
                else None,
                "location": user.profile.business_address if user.profile else None,
                "backdrop_image": user.profile.backdrop_image_url
                if user.profile.backdrop_image_url
                else None,
                "opening_hour": user.profile.opening_hours if user.profile else None,
                "closing_hour": user.profile.closing_hours if user.profile else None,
                "rating": await get_vendor_average_rating(user.id, db),
            }
        )

    return response


async def upload_image_profile(
    current_user: User,
    profile_image_url: UploadFile,
    backdrop_image_url: UploadFile,
    db: AsyncSession,
) -> ProfileImageResponseSchema:
    result = await db.execute(
        select(Profile)
        .json(Profile.profile_image)
        .where(Profile.user_id == current_user.id)
    )

    profile = await result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found!"
        )

    try:
        if profile.profile_image:
            await update_image(profile.profile_image.profile_image_url)
            await update_image(profile.profile_image.backdrop_image_url)

            profile.profile_image.profile_image_url = add_profile_image(
                profile_image_url
            )
            profile.profile_image.backdrop_image_url = add_profile_image(
                backdrop_image_url
            )

            await db.commit()
            await db.refresh()

        else:
            profile_image_url = add_profile_image(profile_image_url)
            backdrop_image_url = add_profile_image(backdrop_image_url)

            profile_image = ProfileImage(
                profile_id=profile.id,
                profile_image_url=profile_image_url,
                backdrop_image_url=backdrop_image_url,
            )

            db.add(profile_image)
            await db.commit()
            await db.refresh(backdrop_image_url)

        return profile.profile_image

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Something went wrong! {e}"
        )


# <<<<< --------- GET LAUNDRY SERVICE PROVIDERS ---------- >>>>>
async def get_users_by_laundry_services(db: AsyncSession) -> List[VendorUserResponse]:
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
            .where(Item.item_type == ItemType.LAUNDRY.value)
            .options(
                selectinload(User.profile).selectinload(Profile.profile_image),
                selectinload(User.profile).selectinload(Profile.backdrop),
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
