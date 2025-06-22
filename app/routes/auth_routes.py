from math import e
from uuid import UUID
from fastapi import (
    APIRouter,
    Depends,
    BackgroundTasks,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_mail import FastMail, MessageSchema

from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import create_tokens, get_current_user
from app.database.database import get_db
from app.models.models import User
from app.schemas.user_schemas import (
    AdminSessionResponse,
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    RiderCreate,
    SessionResponse,
    TokenResponse,
    UserBase,
    UserCreate,
    VerificationSchema,
    CreateUserSchema,
)
from app.services import auth_service
from app.config.config import email_conf


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login", status_code=status.HTTP_200_OK)
async def login_user(
    request: Request,
    user_credentials: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        user = await auth_service.login_user(login_data=user_credentials, db=db)

        token = await create_tokens(
            user_id=user.id,
            email=user.email,
            account_status=user.account_status,
            user_type=user.user_type,
            db=db,
        )
        if user:
            await auth_service.create_session(db, user.id, request)
        return TokenResponse(
            refresh_token=token.refresh_token,
            user_type=token.user_type,
            account_status=token.account_status,
            access_token=token.access_token,
        )

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/logout", include_in_schema=False, status_code=status.HTTP_200_OK)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Logout user by revoking their refresh token"""
    try:
        success = await auth_service.logout_user(db=db, current_user=current_user)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token"
            )
        return {"message": "Successfully logged out"}

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: CreateUserSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> UserBase:
    """Logout user by revoking their refresh token"""

    return await auth_service.create_user(
        db=db, user_data=user_data, background_tasks=background_tasks
    )


@router.post(
    "/register-rider", include_in_schema=False, status_code=status.HTTP_201_CREATED
)
async def create_user(
    data: RiderCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserBase:
    """Logout user by revoking their refresh token"""

    return await auth_service.create_new_rider(
        db=db, data=data, current_user=current_user, background_tasks=background_tasks
    )


@router.get("/sessions", include_in_schema=False, response_model=list[SessionResponse])
async def list_sessions(
    active_only: bool = Query(False, description="Show only active sessions"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all sessions for the current user"""
    return await auth_service.get_user_sessions(db, current_user, active_only)


@router.get(
    "/admin/sessions",
    include_in_schema=False,
    response_model=list[AdminSessionResponse],
)
async def list_all_sessions(
    user_id: UUID = Query(None, description="Filter by user ID"),
    active_only: bool = Query(False, description="Show only active sessions"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all sessions (admin only)"""
    return await auth_service.get_all_user_sessions(
        db, current_user, user_id, active_only, skip, limit
    )


@router.delete(
    "/sessions/{session_id}",
    include_in_schema=False,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def terminate_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Terminate a specific session"""
    await auth_service.terminate_session(db, current_user, session_id)


@router.get("/verify-reset-token/{token}")
async def verify_token(token: str, db: AsyncSession = Depends(get_db)):
    """
    Verify reset token when user clicks email link
    """
    return await auth_service.verify_reset_token(token, db)


@router.post("/verify-contacts")
async def verify_user_contacts(
    verification_data: VerificationSchema,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify user's email and phone"""
    return await auth_service.verify_user_contact(
        verification_data.email_code, verification_data.phone_code, db
    )


@router.put("/resend-verification")
async def resend_verification_codes(email: EmailStr, db: AsyncSession = Depends(get_db)) -> dict:
    """Resend verification codes"""
    email_code, phone_code = await auth_service.generate_resend_verification_code(email=email, db=db)
    return await auth_service.send_verification_codes(email_code=email_code, phone_code=phone_code, db=db)


# <<<<< ------------- PASSWORD CHANGE ------------ >>>>>


@router.post("/recover-password", status_code=status.HTTP_200_OK)
async def password_recovery(
    email_data: PasswordResetRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Request password recovery email"""
    return await auth_service.recover_password(email_data.email, db)


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password_confirm(
    reset_data: PasswordResetConfirm, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Reset password using token"""
    return await auth_service.reset_password(reset_data, db)


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def update_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Change password for logged in user"""
    return await auth_service.change_password(current_user, password_data, db)


@router.post("/logout", include_in_schema=False, status_code=status.HTTP_200_OK)
async def logout_all(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    """Logout from all devices"""
    await auth_service.logout_all_sessions(db, current_user.id)

    return {"message": f" You're logged out from all devices"}


# @router.get("/test-email")
# async def test_email():
#     """Send a test password reset email to verify email and template setup."""
#     message = MessageSchema(
#         subject="Password Reset Request",
#         recipients=['hopearemoh@yahoo.com'],
#         template_body={
#             "reset_url": "https://example.com/reset-password?token=123456",
#             "user": 'Hope Aremoh',
#             "expires_in": "24 hours",
#             "code": "123456"
#         },
#         subtype="html",
#     )
#     fm = FastMail(email_conf)
#     await fm.send_message(message, template_name="email.html")
#     return {"message": f"Test email sent to {'Hope Aremoh'}"}


# @router.get("/test-resend-email")
# async def test_resend_email():
#     """Send a dummy verification email to a hardcoded recipient for testing."""
#     message = MessageSchema(
#         subject="Verify Your Email",
#         recipients=["hopemoh2020@gmail.com"],
#         template_body={
#             "code": "654321",
#             "expires_in": "10 minutes",
#         },
#         subtype="html",
#     )
#     fm = FastMail(email_conf)
#     await fm.send_message(message, template_name="email.html")
#     return {"message": "Dummy verification email sent to Hope Aremoh"}


# @router.get("/test-welcome-email")
# async def test_welcome_email():
#     """Send a dummy welcome email to a hardcoded recipient for testing."""
#     message = MessageSchema(
#         subject="Welcome to ServiPal!",
#         recipients=["kenneth.aremoh@gmail.com"],
#         template_body={
#             "title": "Welcome to ServiPal",
#             "name": "kenneth.aremoh@gmail.com".split('@')[0],
#             "body": "Thank you for joining our platform. We're excited to have you!",
#             "code": "",
#         },
#         subtype="html",
#     )
#     fm = FastMail(email_conf)
#     await fm.send_message(message, template_name="welcome_email.html")
#     return {"message": "Dummy welcome email sent to Hope Aremoh"}
