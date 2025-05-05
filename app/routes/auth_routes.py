from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import create_tokens, get_current_user
from app.database.database import get_db
from app.models.models import User
from app.schemas.user_schemas import AdminSessionResponse, PasswordChange, PasswordResetConfirm, PasswordResetRequest, RiderCreate, SessionResponse, TokenResponse, UserBase, UserCreate
from app.services import auth_service


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login", status_code=status.HTTP_200_OK)
async def login_user(
    request: Request,
    user_credentials: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        user = await auth_service.login_user(login_data=user_credentials, db=db)

        token = await create_tokens(user_id=user.id, user_type=user.user_type, db=db)
        if user:
            await auth_service.create_session(db, user.id, request)
        return TokenResponse(
            refresh_token=token.refresh_token,
            user_type=token.user_type,
            access_token=token.access_token,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    refresh_token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Logout user by revoking their refresh token"""
    try:
        success = await auth_service.logout_user(db=db, refresh_token=refresh_token)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token"
            )
        return {"message": "Successfully logged out"}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserBase:
    """Logout user by revoking their refresh token"""

    return await auth_service.create_user(db=db, user_data=user_data)


@router.post("/register-rider", status_code=status.HTTP_201_CREATED)
async def create_user(
    data: RiderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserBase:
    """Logout user by revoking their refresh token"""

    return await auth_service.create_new_rider(
        db=db, data=data, current_user=current_user
    )


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    active_only: bool = Query(False, description="Show only active sessions"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all sessions for the current user"""
    return await auth_service.get_user_sessions(db, current_user, active_only)


@router.get("/admin/sessions", response_model=list[AdminSessionResponse])
async def list_all_sessions(
    user_id: UUID = Query(None, description="Filter by user ID"),
    active_only: bool = Query(False, description="Show only active sessions"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all sessions (admin only)"""
    return await auth_service.get_all_user_sessions(
        db,
        current_user,
        user_id,
        active_only,
        skip,
        limit
    )

# <<<<< ------------- PASSWORD CHANGE ------------ >>>>>


@router.post("/recover-password", status_code=status.HTTP_200_OK)
async def password_recovery(
    email_data: PasswordResetRequest,
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Request password recovery email"""
    return await auth_service.recover_password(email_data.email, db)


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password_confirm(
    reset_data: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Reset password using token"""
    return await auth_service.reset_password(reset_data, db)


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def update_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Change password for logged in user"""
    return await auth_service.change_password(current_user, password_data, db)


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Logout from all devices"""
    await auth_service.logout_all_sessions(db, current_user.id)
    return {"message": "Successfully logged out from all devices"}
