from datetime import datetime, timedelta
import secrets
from uuid import UUID
from datetime import datetime
from fastapi import BackgroundTasks, HTTPException, Request, status

# from psycopg2 import IntegrityError
from fastapi_mail import FastMail, MessageSchema
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, update, or_
from sqlalchemy.orm import joinedload
from passlib.context import CryptContext

from app.schemas.status_schema import AccountStatus, UserType
from app.schemas.user_schemas import (
    PasswordChange,
    PasswordResetConfirm,
    RiderCreate,
    UserBase,
    UserCreate,
    UserLogin,
    CreateUserSchema,
)
from app.models.models import Profile, Session, User, RefreshToken, Wallet
from app.config.config import settings, email_conf
from app.utils.utils import (
    check_login_attempts,
    record_failed_attempt,
    send_sms,
    validate_password,
)
from app.config.config import redis_client

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


async def login_user(db: AsyncSession, login_data: UserLogin) -> User:
    """
    Args:
            db: Database session
            login_data: Login credentials

    Returns:
            Authenticated user or None if authentication fails
    """

    # Check for account lockout
    check_login_attempts(login_data.username, redis_client)

    # Find user by username
    stmt = select(User).where(User.email == login_data.username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(login_data.password, user.password):
        # Record failed attempt
        record_failed_attempt(login_data.username, redis_client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    # Reset failed attempts on successful login
    redis_client.delete(f"login_attempts:{login_data.username}")

    return user


async def create_user(
    db: AsyncSession, user_data: CreateUserSchema, background_tasks: BackgroundTasks
) -> UserBase:
    """
    Create a new user in the database.
    Args:
        db: Database session
        user_data: User data from request
    Returns:
        The newly created user
    """
    # validate password
    validate_password(user_data.password)
    
    # Check if email already exists
    email_exists_result = await db.execute(
        select(User).where(User.email == user_data.email)
    )
    email_exists = email_exists_result.scalar_one_or_none()
    
    if email_exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Email or Phone number already registered"
        )
    
    # Check if phone number already exists
    # Format the phone number first
    formatted_phone = f"234{user_data.phone_number[1:] if user_data.phone_number.startswith('0') else user_data.phone_number}"
    
    phone_exists_result = await db.execute(
        select(Profile).where(Profile.phone_number == formatted_phone)
    )
    phone_exists = phone_exists_result.scalar_one_or_none()
    
    if phone_exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="Email or Phone number already registered"
        )
    
    try:
        # Create the user
        user = User(
            email=user_data.email.lower(),
            password=hash_password(user_data.password),
            user_type=user_data.user_type,
            updated_at=datetime.now(),
        )
        # Add user to database
        db.add(user)
        await db.flush()
        
        profile = Profile(
            user_id=user.id,
            phone_number=formatted_phone,
        )
        db.add(profile)
        
        if user.user_type != UserType.RIDER:
            # Create user wallet
            wallet = Wallet(id=user.id, balance=0, escrow_balance=0)
            db.add(wallet)
            
        await db.commit()
        await db.refresh(profile)
        await db.refresh(user)
        
        redis_client.delete("all_users")
        
        # Generate and send verification codes
        email_code, phone_code = await generate_verification_codes(user, db)
        
        # Send verification code to phone and email
        await send_verification_codes(user=user, email_code=email_code, phone_code=phone_code, db=db)
        
        return user
        
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or phone number already registered",
        )


# async def create_user(
#     db: AsyncSession, user_data: CreateUserSchema, background_tasks: BackgroundTasks
# ) -> UserBase:
#     """
#     Create a new user in the database.
#     Args:
#         db: Database session
#         user_data: User data from request
#     Returns:
#         The newly created user
#     """
#     # validate password
#     validate_password(user_data.password)

#     # Check if email already exists
#     # user_exists_result = await db.execute(
#     #     select(User)
#     #     .options(joinedload(User.profile))
#     #     .where(User.email == user_data.email)
#     # )
#     # user_exists = user_exists_result.scalar_one_or_none()

#     user_exists_result = await db.execute(
#     select(User)
#     .options(joinedload(User.profile))
#     .where(
#         or_(
#             User.email == user_data.email,
#             User.profile.has(Profile.phone_number == user_data.phone_number))))
#     user_exists = user_exists_result.scalar_one_or_none()


#     # If user exists, check if email or phone number is already registered
#     if user_exists:
#         raise HTTPException(
#             status_code=status.HTTP_409_CONFLICT, detail="Email or phone number already registered"
#         )

#     try:
#         # Create the user
#         user = User(
#             email=user_data.email.lower(),
#             password=hash_password(user_data.password),
#             user_type=user_data.user_type,
#             updated_at=datetime.now(),
#         )

#         # Add user to database
#         db.add(user)
#         await db.flush()

#         profile = Profile(
#             user_id=user.id,
#             phone_number=f"234{user_data.phone_number[1:] if user_data.phone_number.startswith(str(0)) else user_data.phone_number}",
#         )
#         db.add(profile)

#         if user.user_type != UserType.RIDER:
#             # Create user wallet
#             wallet = Wallet(id=user.id, balance=0, escrow_balance=0)
#             db.add(wallet)

#         await db.commit()
#         await db.refresh(profile)
#         await db.refresh(user)

#         redis_client.delete("all_users")

#         # Generate and send verification codes
#         email_code, phone_code = await generate_verification_codes(user, db)

#         # Send verification code to phone and email
#         # background_tasks.add_task(
#         #     send_verification_codes, user, email_code, phone_code, db
#         # )
#         await send_verification_codes(email_code=email_code, phone_code=phone_code, db=db)
#         # await send_sms(phone_number=profile.phone_number, phone_code=profile.ph)
#         return user
#     except IntegrityError as e:
#         await db.rollback()
#         raise HTTPException(
#             status_code=status.HTTP_409_CONFLICT,
#             detail="Email or phone number already registered",
#         )


# CREATE RIDER


async def create_new_rider(
    data: RiderCreate,
    db: AsyncSession,
    current_user: User,
    background_tasks: BackgroundTasks,
) -> UserBase:
    """
    Creates a new rider user and assigns them to the current dispatch user.

    Args:
        data (RiderCreate): The rider details.
        db: The database session.
        current_user: The current user.
        background_tasks: Background tasks runner.

    Returns:
        UserBase: The newly created rider user details.
    """

    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.dispatcher_id == current_user.id)
    )
    riders_result = await db.execute(stmt)
    riders_count = riders_result.scalar()

    # Check user permissions and restrictions
    if current_user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot create rider due to suspension!",
        )
    if not current_user.is_verified and riders_count > 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your business to add more riders",
        )

    if current_user.account_status == AccountStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Please verify your account!"
        )

    if current_user.user_type != UserType.DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a dispatch user can create rider.",
        )

    if not current_user.profile.business_registration_number and riders_count > 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please update your company registration number to add more riders.",
        )
    if not current_user.user_type == UserType.DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only dispatch company users can create riders!",
        )

    if (
        not current_user.profile.business_name
        or not current_user.profile.business_address
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please update your profile with your company name and phone number.",
        )

    # validate password
    validate_password(data.password)

    # Check if email already exists
    user_exists_result = await db.execute(
        select(User).options(joinedload(User.profile)).where(User.email == data.email)
    )
    user_exists = user_exists_result.scalar_one_or_none()

    if user_exists.email == data.email or user_exists.profile.phone_number == data.phone_number:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already or phone number already registered"
        )

    # Create new rider and assign to current dispatch
    try:
        new_rider = User(
            email=data.email.lower(),
            password=hash_password(data.password),
            user_type=UserType.RIDER,
            dispatcher_id=current_user.id,
            created_at=datetime.today(),
            updated_at=datetime.today(),
        )

        db.add(new_rider)
        await db.flush()

        rider_profile = Profile(
            user_id=new_rider.id,
            full_name=data.full_name,
            phone_number=f"234{data.phone_number[1:] if data.phone_number.startswith(str(0)) else data.phone_number}",
            bike_number=data.bike_number,
            created_at=datetime.today(),
            updated_at=datetime.today(),
            business_address=current_user.profile.business_address,
            business_name=current_user.profile.business_name,
            # add company email
            # add company phone
        )
        db.add(rider_profile)

        await db.commit()
        await db.refresh(new_rider)

        rider_dict = {
            "user_type": new_rider.user_type,
            "email": new_rider.email,
        }

        redis_client.delete("all_users")

        # Generate and send verification codes
        email_code, phone_code = await generate_verification_codes(new_rider, db)

        # Send verification code to phone and email
        # background_tasks.add_task(
        #     send_verification_codes, new_rider, email_code, phone_code, db
        # )
        await send_verification_codes(email_code=email_code, phone_code=phone_code, db=db)

        invalidate_rider_cache(current_user.id)
        return UserBase(**rider_dict)
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Rider with this data exists!"
        )


async def create_session(db: AsyncSession, user_id: UUID, request: Request) -> Session:
    """Create new session record"""
    session = Session(
        user_id=user_id,
        device_info=request.headers.get("user-agent", "unknown"),
        ip_address=request.client.host,
        is_active=True,
    )
    db.add(session)
    await db.commit()
    return session


async def delete_rider(current_user: User, db: AsyncSession, rider_id: UUID) -> None:
    if current_user.user_type != UserType.DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission denied"
        )
    stmt = (
        select(User)
        .where(User.id == rider_id)
        .where(User.dispatcher_id == current_user.id)
    )
    result = await db.execute(stmt)
    rider = result.scalar_one_or_none()

    if not rider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rider not found"
        )

    await db.delete(rider)
    await db.commit()
    return None


async def recover_password(email: str, db: AsyncSession) -> dict:
    """
    Initiates password recovery process by sending reset token via email

    Args:
        email: User's email address
        db: Database session

    Returns:
        dict: Message confirming reset email sent
    """
    # Find user by email
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found with this email",
        )

    # Generate reset token
    reset_token = secrets.token_urlsafe(32)
    token_expires = datetime.now() + timedelta(hours=24)

    # Save reset token to user record
    user.reset_token = reset_token
    user.reset_token_expires = token_expires
    await db.commit()

    # Send reset email with frontend URL
    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"

    # Send reset email
    message = MessageSchema(
        subject="Password Reset Request",
        recipients=[email],
        template_body={
            "reset_url": reset_url,
            "user": user.email,
            "expires_in": "24 hours",
        },
        subtype="html",
    )

    fm = FastMail(email_conf)
    await fm.send_message(message, template_name="reset_password.html")

    return {"message": "Password reset instructions sent to your email"}


async def verify_reset_token(token: str, db: AsyncSession) -> bool:
    """
    Verify if reset token is valid and not expired
    """
    stmt = select(User).where(
        User.reset_token == token, User.reset_token_expires > datetime.now()
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    return True


async def change_password(
    current_user: User, password_data: PasswordChange, db: AsyncSession
) -> dict:
    """
    Changes user's password after verifying current password

    Args:
        current_user: Currently authenticated user
        password_data: New and current password
        db: Database session

    Returns:
        dict: Success message
    """
    # validate password
    validate_password(password_data.new_password)

    # Verify current password
    if not verify_password(password_data.current_password, current_user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Update password
    current_user.password = hash_password(password_data.new_password)
    current_user.updated_at = datetime.now()

    try:
        await db.commit()
        # Logout from all devices
        await logout_user(db, current_user.id)
        return {"message": "Password changed successfully"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to change password: {str(e)}",
        )


async def logout_user(db: AsyncSession, current_user: User) -> bool:
    """
    Revokes all refresh tokens for a user, effectively logging them out of all devices
    Args:
        db: Database session
        user_id: ID of user to logout
    Returns:
        bool: True if successful
    """
    try:
        stmt = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == current_user.id,
                RefreshToken.is_revoked == False,
            )
            .values(is_revoked=True, revoked_at=datetime.now())
        )
        await db.execute(stmt)
        await db.commit()
        redis_client.delete(f"current_useer_profile:{current_user.id}")
        return True
    except Exception as e:
        await db.rollback()
        # Instead of raising, just return True for UI logout success
        return True


async def reset_password(reset_data: PasswordResetConfirm, db: AsyncSession) -> dict:
    """Reset password using reset token"""

    # Validate password first
    validate_password(reset_data.new_password)

    # Find user with valid reset token
    stmt = select(User).where(
        User.reset_token == reset_data.token, User.reset_token_expires > datetime.now()
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    try:
        # Update password
        user.password = hash_password(reset_data.new_password)
        # Clear reset token
        user.reset_token = None
        user.reset_token_expires = None
        user.updated_at = datetime.now()

        await db.commit()

        # Log out from all devices for security
        await logout_user(db, user)

        return {"message": "Password reset successful"}

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset password: {str(e)}",
        )


async def get_user_sessions(
    db: AsyncSession, current_user: User, active_only: bool = False
) -> list[Session]:
    """
    Get all sessions for a user

    Args:
        db: Database session
        current_user: Currently authenticated user
        active_only: If True, return only active sessions

    Returns:
        list[Session]: List of user sessions
    """
    try:
        query = (
            select(Session)
            .where(Session.user_id == current_user.id)
            .order_by(Session.last_active.desc())
        )

        if active_only:
            query = query.where(Session.is_active == True)

        result = await db.execute(query)
        sessions = result.scalars().all()

        return sessions

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch sessions: {str(e)}",
        )


async def get_all_user_sessions(
    db: AsyncSession,
    current_user: User,
    user_id: UUID = None,
    active_only: bool = False,
    skip: int = 0,
    limit: int = 50,
) -> list[Session]:
    """
    Get all sessions (admin only)

    Args:
        db: Database session
        current_user: Currently authenticated user (must be admin)
        user_id: Optional - filter by specific user
        active_only: If True, return only active sessions
        skip: Number of records to skip
        limit: Maximum number of records to return

    Returns:
        list[Session]: List of all sessions
    """
    if current_user.user_type != UserType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )

    try:
        query = (
            select(Session)
            .options(joinedload(Session.user))
            .order_by(Session.last_active.desc())
            .offset(skip)
            .limit(limit)
        )

        if user_id:
            query = query.where(Session.user_id == user_id)

        if active_only:
            query = query.where(Session.is_active == True)

        result = await db.execute(query)
        sessions = result.scalars().all()

        return sessions

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch sessions: {str(e)}",
        )


async def terminate_session(
    db: AsyncSession, current_user: User, session_id: UUID
) -> None:
    """
    Terminate a specific session and revoke associated tokens
    Admins can terminate any session, users can only terminate their own
    """
    try:
        # Build base query
        stmt = select(Session).where(Session.id == session_id)

        # If not admin, restrict to user's own sessions
        if current_user.user_type != UserType.ADMIN:
            stmt = stmt.where(Session.user_id == current_user.id)

        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or access denied",
            )

        # Revoke refresh tokens associated with this session
        await db.execute(
            update(RefreshToken)
            .where(
                # Use session.user_id instead of current_user.id
                RefreshToken.user_id == session.user_id,
                RefreshToken.session_id == session_id,
            )
            .values(is_revoked=True, revoked_at=datetime.now())
        )

        # Deactivate the session
        session.is_active = False
        session.last_active = datetime.now()

        # Add termination metadata
        session.terminated_by = current_user.id
        session.termination_reason = (
            "Terminated by admin"
            if current_user.user_type == UserType.ADMIN
            else "User logout"
        )

        await db.commit()

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to terminate session: {str(e)}",
        )


async def generate_2fa_code(user: User, db: AsyncSession) -> str:
    """Generate 2FA code and send to user's email"""
    code = secrets.randbelow(1000000)
    formatted_code = f"{code:06d}"  # Ensure 6 digits

    user.two_factor_code = formatted_code
    user.two_factor_expires = datetime.now() + timedelta(minutes=10)
    await db.commit()

    # Send code via email
    message = MessageSchema(
        subject="Your 2FA Code",
        recipients=[user.email],
        template_body={"code": formatted_code},
        subtype="html",
    )

    fm = FastMail(email_conf)
    await fm.send_message(message, template_name="2fa_code.html")
    return formatted_code


async def generate_resend_verification_code(email: str, db: AsyncSession):
    user_result = await db.execute(select(User).where(User.email == email))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
        # Generate codes
    email_code = f"{secrets.randbelow(1000000):06d}"
    phone_code = f"{secrets.randbelow(1000000):06d}"

    # Set expiration time (10 minutes from now)
    expires = datetime.now() + timedelta(minutes=25)

    # Update user record with codes and expiration
    user.email_verification_code = email_code
    user.profile.phone_verification_code = phone_code

    user.email_verification_expires = expires
    user.profile.phone_verification_expires = expires

    await db.commit()

    return email_code, phone_code



async def generate_verification_codes(user: User, db: AsyncSession) -> tuple[str, str]:
    """Generate verification codes for both email and phone"""

    # Generate codes
    email_code = f"{secrets.randbelow(1000000):06d}"
    phone_code = f"{secrets.randbelow(1000000):06d}"

    # Set expiration time (10 minutes from now)
    expires = datetime.now() + timedelta(minutes=25)

    # Update user record with codes and expiration
    user.email_verification_code = email_code
    user.profile.phone_verification_code = phone_code

    user.email_verification_expires = expires
    user.profile.phone_verification_expires = expires

    await db.commit()

    return email_code, phone_code


# async def send_verification_codes(
#     email_code: str, phone_code: str, db: AsyncSession
# ) -> dict:
#     """Send verification codes via email and SMS"""

#     stmt = select(User).options(joinedload(User.profile))
#     result = await db.execute(stmt)
#     user = result.scalar_one_or_none()

#     if not user.profile.phone_number and not user.email:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="User phone number or email not found",
#         )

#     # Send email code
#     message = MessageSchema(
#         subject="Verify Your Email",
#         recipients=[user.email],
#         template_body={"code": email_code, "expires_in": "10 minutes"},
#         subtype="html",
#     )

#     fm = FastMail(email_conf)
#     await fm.send_message(message, template_name="email.html")

#     # Send SMS code (using Termii)
#     await send_sms(
#         phone_number=user.profile.phone_number,
#         phone_code=f"Your verification code is: {phone_code}. This code will expire in 10 minutes.",
#     )

#     return {"message": "Verification codes sent to your email and phone"}


async def send_verification_codes(
    user: User, email_code: str, phone_code: str, db: AsyncSession
) -> dict:
    """Send verification codes via email and SMS"""
    
    # Load the profile if not already loaded
    if not user.profile:
        await db.refresh(user, ["profile"])
    
    if not user.profile.phone_number and not user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User phone number or email not found",
        )
    
    # Send email code
    message = MessageSchema(
        subject="Verify Your Email",
        recipients=[user.email],
        template_body={"code": email_code, "expires_in": "10 minutes"},
        subtype="html",
    )
    fm = FastMail(email_conf)
    await fm.send_message(message, template_name="email.html")
    
    # Send SMS code (using Termii)
    await send_sms(
        phone_number=user.profile.phone_number,
        phone_code=f"Your verification code is: {phone_code}. This code will expire in 10 minutes.",
    )
    return {"message": "Verification codes sent to your email and phone"}


async def verify_user_contact(
    email_code: str, phone_code: str, db: AsyncSession
) -> dict:
    """Verify both email and phone codes"""
    now = datetime.now()
    # Load user with profile
    stmt = select(User).options(joinedload(User.profile))
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user.profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="User profile not found"
        )
    # Check if codes are expired
    email_expired = (
        user.email_verification_expires is not None
        and user.email_verification_expires < now
    )
    phone_expired = (
        user.profile.phone_verification_expires is not None
        and user.profile.phone_verification_expires < now
    )
    if email_expired or phone_expired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification codes have expired",
        )
    # Verify email code
    if email_code != user.email_verification_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email verification code",
        )
    # Verify phone code
    if phone_code != user.profile.phone_verification_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone verification code",
        )
    # Update verification status
    user.is_email_verified = True
    user.profile.is_phone_verified = True
    user.account_status = AccountStatus.CONFIRMED
    user.email_verification_code = None
    user.profile.phone_verification_code = None
    user.email_verification_expires = None
    user.profile.phone_verification_expires = None
    await db.commit()
    await send_welcome_email(user)
    return {"message": "Email and phone verified successfully"}


# Function to invalidate cache when rider data changes
def invalidate_rider_cache(dispatcher_id: UUID):
    """Invalidate rider cache when data changes"""
    pattern = f"dispatcher:{dispatcher_id}:riders:*"
    keys = redis_client.keys(pattern)
    if keys:
        redis_client.delete(*keys)
        # logger.info(f"Cache invalidated for pattern: {pattern}")




async def send_welcome_email(user):
    message = MessageSchema(
        subject="Welcome to ServiPal!",
        recipients=[user.email],
        template_body={
            "title": "Welcome to ServiPal",
            "name": user.email.split("@")[0],
            "body": "Thank you for joining our platform. We're excited to have you!",
            "code": "",
        },
        subtype="html",
    )
    fm = FastMail(email_conf)
    await fm.send_message(message, template_name="welcome_email.html")
