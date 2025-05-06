from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status


from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import create_tokens, get_current_user
from app.database.database import get_db
from app.models.models import ProfileImage, User, Profile

from app.schemas.user_schemas import (
    ProfileSchema,
    UserProfileResponse,
    UserResponse,
    WalletRespose,
    WalletSchema,
)
from app.services import user_service
from app.utils.s3_service import add_profile_image, update_image


router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("", status_code=status.HTTP_200_OK)
async def get_users(
    db: AsyncSession = Depends(get_db),
) -> list[UserResponse]:

    return await user_service.get_users(db=db)


@router.get("/wallets", status_code=status.HTTP_200_OK)
async def get_user_wallets(
    db: AsyncSession = Depends(get_db),
) -> list[WalletSchema]:

    return await user_service.get_user_wallets(db=db)


@router.post("/profile", status_code=status.HTTP_201_CREATED)
async def create_user_profile(
    profile_data: ProfileSchema,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileSchema:

    return await user_service.create_pofile(
        profile_data=profile_data, db=db, current_user=current_user
    )


@router.post("/upload-image", status_code=status.HTTP_201_CREATED)
async def upload_profile_image(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload profile image"""
    try:
        url = await add_profile_image(
            image,
            folder=f"profiles/{current_user.id}"
        )

        # Update user profile
        profile = await db.get(Profile, current_user.id)
        if profile and profile.profile_image:
            # Update existing image
            url = await update_image(
                image,
                profile.profile_image.url,
                folder=f"profiles/{current_user.id}"
            )

        profile_image = ProfileImage(url=url, profile_id=profile.id)
        db.add(profile_image)
        await db.commit()

        return {"url": url}

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
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
