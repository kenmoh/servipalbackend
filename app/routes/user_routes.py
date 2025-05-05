from uuid import UUID
from annotated_types import T
from fastapi import APIRouter, Depends, HTTPException, status


from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import create_tokens, get_current_user
from app.database.database import get_db
from app.models.models import User, Profile

from app.schemas.user_schemas import (
    ProfileSchema,
    UserProfileResponse,
    UserResponse,
    WalletRespose,
    WalletSchema,
)
from app.services import user_service


router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("", status_code=status.HTTP_200_OK)
async def get_users(
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:
    T
    return await user_service.get_users(db=db)


@router.get("/wallets", status_code=status.HTTP_200_OK)
async def get_user_wallets(
    db: AsyncSession = Depends(get_db),
) -> list[WalletSchema]:
    T
    return await user_service.get_user_wallets(db=db)


@router.post("/profile", status_code=status.HTTP_201_CREATED)
async def create_user_profile(
    profile_data: ProfileSchema,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileSchema:
    T
    return await user_service.create_pofile(
        profile_data=profile_data, db=db, current_user=current_user
    )


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
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    return await user_service.get_user_with_profile(db=db, user_id=user_id)
