import asyncio
from datetime import datetime, timedelta, time
import secrets
from uuid import UUID
from fastapi import HTTPException, Request, status

# from psycopg2 import IntegrityError
from fastapi_mail import FastMail, MessageSchema
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, update, or_
from sqlalchemy.orm import joinedload, selectinload
from passlib.context import CryptContext

from app.schemas.status_schema import AccountStatus, UserType
from app.schemas.user_schemas import (
    PasswordChange,
    PasswordResetConfirm,
    RiderCreate,
    StaffCreate,
    UserBase,
    UserLogin,
    CreateUserSchema,
    UpdateStaffSchema,
)
from app.models.models import AuditLog, Profile, Session, User, RefreshToken, Wallet
from app.services import ws_service
from app.schemas.user_schemas import UpdateStaffSchema
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

    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You account has been blocked. Contact support",
        )

    if not user or not verify_password(login_data.password, user.password):
        # Record failed attempt
        record_failed_attempt(login_data.username, redis_client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    # Reset failed attempts on successful login
    redis_client.delete(f"login_attempts:{login_data.username}")

    return user


async def login_admin_user(db: AsyncSession, login_data: UserLogin) -> User:
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

    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You account has been blocked. Contact support",
        )

    if not user or not verify_password(login_data.password, user.password):
        # Record failed attempt
        record_failed_attempt(login_data.username, redis_client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    if user.user_type not in [UserType.ADMIN, UserType.MODERATOR, UserType.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )

    # Reset failed attempts on successful login
    redis_client.delete(f"login_attempts:{login_data.username}")

    return user


async def create_user(db: AsyncSession, user_data: CreateUserSchema) -> UserBase:
    """
    Create a new user in the database.
    Optimized version using database constraints for validation.

    Args:
        db: Database session
        user_data: User data from request
    Returns:
        The newly created user
    """
    # Validate password
    validate_password(user_data.password)

    # Format the phone number
    formatted_phone = f"234{user_data.phone_number[1:] if user_data.phone_number.startswith('0') else user_data.phone_number}"

    try:
        # Create the user - let database constraints handle email uniqueness
        user = User(
            email=user_data.email.lower(),
            password=hash_password(user_data.password),
            user_type=user_data.user_type,
            updated_at=datetime.now(),
        )

        # Add user to database
        db.add(user)
        await db.flush()

        # Create profile
        profile = Profile(
            user_id=user.id,
            phone_number=formatted_phone,
        )
        db.add(profile)

        # Create wallet for non-rider users
        if user.user_type != UserType.RIDER:
            wallet = Wallet(id=user.id, balance=0, escrow_balance=0)
            db.add(wallet)

        await db.commit()
        await db.refresh(profile)
        await db.refresh(user)

        # Generate and send verification codes
        email_code, phone_code = await generate_verification_codes(user, db)

        # Send verification code to phone and email
        await send_verification_codes(
            user=user, email_code=email_code, phone_code=phone_code, db=db
        )
        redis_client.delete("all_users")
        await asyncio.sleep(0.1)
        await ws_service.broadcast_new_user(
            {"email": user.email, "user_type": user.user_type}
        )

        return user

    except IntegrityError as e:
        await db.rollback()

        # Parse the constraint violation to provide specific error messages
        error_msg = str(e).lower()

        if "email" in error_msg and "unique" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
            )
        elif "phone_number" in error_msg and "unique" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number already registered",
            )
        else:
            # Generic fallback for other integrity errors
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email or phone number already registered",
            )


async def create_new_rider(
    data: RiderCreate,
    db: AsyncSession,
    current_user: User,
) -> UserBase:
    """
    Creates a new rider user and assigns them to the current dispatch user.
    Ultra-optimized version using database constraints for validation.
    """

    # Single query to get rider count for business logic checks
    riders_count = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.dispatcher_id == current_user.id)
    )

    # Check user permissions and restrictions
    if current_user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot create rider due to suspension!",
        )

    if not current_user.is_verified and riders_count > 2:
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

    if not current_user.profile.business_registration_number and riders_count > 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please update your company registration number to add more riders.",
        )

    if (
        not current_user.profile.business_name
        or not current_user.profile.business_address
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please update your profile with your company name and phone number.",
        )

    # Validate password
    validate_password(data.password)

    # Format phone number
    formatted_phone = f"234{data.phone_number[1:] if data.phone_number.startswith('0') else data.phone_number}"

    # Create new rider and let database constraints handle uniqueness validation
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
            phone_number=formatted_phone,
            bike_number=data.bike_number,
            created_at=datetime.today(),
            updated_at=datetime.today(),
            business_address=current_user.profile.business_address,
            business_name=current_user.profile.business_name,
        )
        db.add(rider_profile)

        await db.commit()
        await db.refresh(new_rider)

        rider_dict = {
            "user_type": new_rider.user_type,
            "email": new_rider.email,
        }

        # Generate and send verification codes
        email_code, phone_code = await generate_verification_codes(new_rider, db)

        await send_verification_codes(
            user=new_rider, email_code=email_code, phone_code=phone_code, db=db
        )

        invalidate_rider_cache(current_user.id)
        redis_client.delete("all_users")
        await asyncio.sleep(0.1)
        await ws_service.broadcast_new_user(
            {"email": new_rider.email, "user_type": new_rider.user_type}
        )

        return UserBase(**rider_dict)

    except IntegrityError as e:
        await db.rollback()

        error_msg = str(e).lower()

        if "email" in error_msg and "unique" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
            )
        elif "phone_number" in error_msg and "unique" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number already registered",
            )
        elif "bike_number" in error_msg and "unique" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bike number already registered",
            )
        else:
            # Generic fallback for other integrity errors
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Rider with this data already exists!",
            )


async def create_new_staff(
    data: StaffCreate,
    db: AsyncSession,
    current_user: User,
) -> UserBase:
    """
    Creates a new rider user and assigns them to the current dispatch user.
    Ultra-optimized version using database constraints for validation.
    """

    if current_user.user_type not in [UserType.ADMIN, UserType.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin user can create staff.",
        )

    # Validate password
    validate_password(data.password)

    # Format phone number
    formatted_phone = f"234{data.phone_number[1:] if data.phone_number.startswith('0') else data.phone_number}"

    # Create new rider and let database constraints handle uniqueness validation
    try:
        # --- STAFF USER ---
        new_staff = User(
            email=data.email.lower(),
            password=hash_password(data.password),
            user_type=UserType.MODERATOR,
            dispatcher_id=current_user.id,
            created_at=datetime.today(),
            updated_at=datetime.today(),
            is_email_verified=True,
            account_status=AccountStatus.CONFIRMED,
        )

        db.add(new_staff)
        await db.flush()

        # --- STAFF PROFILE ---
        staff_profile = Profile(
            user_id=new_staff.id,
            full_name=data.full_name,
            phone_number=formatted_phone,
            created_at=datetime.today(),
            updated_at=datetime.today(),
            is_phone_verified=True,
            business_address=current_user.profile.business_address,
            business_name=current_user.profile.business_name,
        )

        db.add(staff_profile)
        db.flush()

        # --- AUDIT LOG ---

        audit = AuditLog(
            actor_id=current_user.id,
            actor_name=current_user.profile.full_name or current_user.email,
            actor_role=current_user.user_type,
            action="create_staff",
            resource_type="User",
            resource_id=new_staff.id,
            resource_summary=f"create_staff: {new_staff.email}, {staff_profile.full_name}",
            changes=None,
            extra_metadata=None,
        )

        db.add(audit)

        await db.commit()
        await db.refresh(new_staff)

        staff_dict = {
            "user_type": new_staff.user_type,
            "email": new_staff.email,
        }

        redis_client.delete("all_users")
        redis_client.delete("teams")

        invalidate_rider_cache(current_user.id)

        # await asyncio.sleep(0.1)
        await ws_service.broadcast_new_team(
            {
                "team_id": staff_profile.user_id,
                "email": new_staff.email,
                "full_name": staff_profile.full_name,
                "user_type": new_staff.user_type,
            }
        )

        return UserBase(**staff_dict)

    except IntegrityError as e:
        await db.rollback()

        error_msg = str(e).lower()

        if "email" in error_msg and "unique" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
            )
        elif "phone_number" in error_msg and "unique" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number already registered",
            )
        else:
            # Generic fallback for other integrity errors
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Staff with this data already exists!",
            )



async def update_staff(
    staff_id: UUID,
    data: UpdateStaffSchema,
    db: AsyncSession,
    current_user: User,
) -> UserBase:
    """
    Update a staff (moderator) user's profile. Only admins/super admins can update staff.
    Only checks for uniqueness conflicts if the new value is different from the current value.
    """

    # Check permissions
    if current_user.user_type not in [UserType.ADMIN, UserType.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an Admin user can update staff.",
        )

    # Fetch the staff user and profile
    stmt = select(User).where(User.id == staff_id, User.user_type == UserType.MODERATOR)
    result = await db.execute(stmt)
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found"
        )

    # Fetch profile
    await db.refresh(staff, ["profile"])
    profile = staff.profile
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Staff profile not found"
        )

    # Only check for conflicts if the new value is different from the current value
    conflict_filters = []
    if data.phone_number and data.phone_number != profile.phone_number:
        conflict_filters.append(Profile.phone_number == data.phone_number)
    if data.business_name and data.business_name != profile.business_name:
        conflict_filters.append(Profile.business_name == data.business_name)
    if data.business_address and data.business_address != profile.business_address:
        conflict_filters.append(Profile.business_address == data.business_address)
    if (
        data.bank_account_number
        and data.bank_account_number != profile.bank_account_number
    ):
        conflict_filters.append(Profile.bank_account_number == data.bank_account_number)
    if data.bank_name and data.bank_name != profile.bank_name:
        conflict_filters.append(Profile.bank_name == data.bank_name)
    if (
        data.account_holder_name
        and data.account_holder_name != profile.account_holder_name
    ):
        conflict_filters.append(Profile.account_holder_name == data.account_holder_name)
    if data.full_name and data.full_name != profile.full_name:
        conflict_filters.append(Profile.full_name == data.full_name)

    if conflict_filters:
        stmt = select(Profile).where(
            or_(*conflict_filters), Profile.user_id != staff_id
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One or more fields already registered to another user.",
            )

    # Update profile fields
    old_profile = profile.__dict__.copy()
    for field, value in data.model_dump(exclude_unset=True).items():
        if hasattr(profile, field):
            setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)

    # Invalidate cached data
    invalidate_rider_cache(current_user.id)
    redis_client.delete(f"current_useer_profile:{staff_id}")
    redis_client.delete("all_users")

    # --- AUDIT LOG ---
    changed_fields = {
        k: [old_profile.get(k), getattr(profile, k)]
        for k in data.model_dump(exclude_unset=True).keys()
        if old_profile.get(k) != getattr(profile, k)
    }
    if changed_fields:
        # Convert non-serializable types like datetime and time to strings for JSON
        for key, values in changed_fields.items():
            for i, value in enumerate(values):
                if isinstance(value, (datetime, time)):
                    values[i] = value.isoformat()

        audit = AuditLog(
            actor_id=current_user.id,
            actor_name=current_user.profile.full_name or current_user.email,
            actor_role=current_user.user_type,
            action="update_staff_profile",
            resource_type="Profile",
            resource_id=profile.user_id,
            resource_summary=f"updated user with email: {staff.email}",
            changes=changed_fields,
            metadata=None,
        )
        db.add(audit)
        db.commit()
    return staff


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
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
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


# async def recover_password(email: str, db: AsyncSession) -> dict:
#     """
#     Initiates password recovery process by sending reset token via email

#     Args:
#         email: User's email address
#         db: Database session

#     Returns:
#         dict: Message confirming reset email sent
#     """
#     # Find user by email
#     stmt = select(User).where(User.email == email)
#     result = await db.execute(stmt)
#     user = result.scalar_one_or_none()

#     if not user:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail="No account found with this email",
#         )

#     # Generate reset token
#     reset_token = secrets.token_urlsafe(32)
#     token_expires = datetime.now() + timedelta(hours=24)

#     # Save reset token to user record
#     user.reset_token = reset_token
#     user.reset_token_expires = token_expires
#     await db.commit()

#     # Send reset email with frontend URL
#     reset_url = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"

#     # Send reset email
#     message = MessageSchema(
#         subject="Password Reset Request",
#         recipients=[email],
#         template_body={
#             "reset_url": reset_url,
#             "user": user.email,
#             "expires_in": "24 hours",
#         },
#         subtype="html",
#     )

#     fm = FastMail(email_conf)
#     await fm.send_message(message, template_name="reset_password.html")

#     return {"message": "Password reset instructions sent to your email"}

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
    
    # Send reset email
    message = MessageSchema(
        subject="Password Reset Request",
        recipients=[email],
        template_body={
            "url": settings.FRONTEND_URL, 
            "reset_token": reset_token, 
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
    old_password_hash = current_user.password
    current_user.password = hash_password(password_data.new_password)
    current_user.updated_at = datetime.now()

    try:
        await db.commit()
        # Logout from all devices
        await logout_user(db, current_user.id)
        # --- AUDIT LOG ---

        audit = AuditLog(
            actor_id=current_user.id,
            actor_name=current_user.profile.full_name or current_user.email,
            actor_role=current_user.user_type,
            action="change_password",
            resource_type="User",
            resource_id=current_user.id,
            resource_summary=current_user.email,
            changes={"password": [old_password_hash, "***"]},
            metadata=None,
        )

        db.add(audit)
        await db.commit()
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
    except Exception:
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
        old_password_hash = user.password
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
    # Load user with profile relationship
    user_result = await db.execute(
        select(User).options(joinedload(User.profile)).where(User.email == email)
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not user.profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User profile not found",
        )

    # Generate codes
    email_code = f"{secrets.randbelow(1000000):06d}"
    phone_code = f"{secrets.randbelow(1000000):06d}"

    # Set expiration time (25 minutes from now)
    expires = datetime.now() + timedelta(minutes=25)

    # Update user record with codes and expiration
    user.email_verification_code = email_code
    user.profile.phone_verification_code = phone_code
    user.email_verification_expires = expires
    user.profile.phone_verification_expires = expires

    await db.commit()

    return user, email_code, phone_code


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

    # Single query to get user with profile and verify both codes exist
    user_query = (
        select(User)
        .options(selectinload(User.profile))
        .where(User.email_verification_code == email_code)
    )

    user = await db.scalar(user_query)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid email verification code",
        )

    if not user.profile or user.profile.phone_verification_code != phone_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone verification code",
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


async def update_staff_password(
    staff_id: UUID,
    new_password: str,
    db: AsyncSession,
    current_user: User,
) -> dict:
    """
    Update a staff's password. Only admin/superadmin can do this.
    """
    if current_user.user_type not in [UserType.ADMIN, UserType.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin or superadmin can update staff password.",
        )
    stmt = select(User).where(User.id == staff_id)
    result = await db.execute(stmt)
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found"
        )
    old_password_hash = staff.password
    staff.password = hash_password(new_password)
    staff.updated_at = datetime.now()
    await db.commit()
    # --- AUDIT LOG ---
    audit = AuditLog(
        actor_id=current_user.id,
        actor_name=current_user.profile.full_name or current_user.email,
        actor_role=current_user.user_type,
        action="change_password",
        resource_type="User",
        resource_id=current_user.id,
        resource_summary=current_user.email,
        changes={"password": [old_password_hash, "***"]},
        metadata=None,
    )

    db.add(audit)
    await db.commit()
    return {"message": "Staff password updated successfully."}
