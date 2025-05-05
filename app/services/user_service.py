from uuid import UUID
import json
from fastapi import HTTPException, status

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload, joinedload

from app.config.config import redis_client
from app.models.models import User, Wallet, Profile
from app.schemas.user_schemas import (
    ProfileSchema,
    UserProfileResponse,
    UserResponse,
    WalletSchema,
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
