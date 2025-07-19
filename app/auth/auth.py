import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from pydantic import EmailStr

from app.models.models import User, RefreshToken
from app.schemas.status_schema import AccountStatus
from app.schemas.user_schemas import TokenResponse
from app.database.database import get_db
from app.config.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


async def create_refresh_token(
    user_id: str,
    user_type: str,
    email: EmailStr,
    account_status: AccountStatus,
    db: AsyncSession,
) -> str:
    token = str(uuid.uuid4())
    expires_at = datetime.now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    refresh_token = RefreshToken(
        token=token,
        user_id=user_id,
        user_type=user_type,
        email=email,
        account_status=account_status,
        expires_at=expires_at,
    )

    db.add(refresh_token)
    await db.commit()

    return token


async def create_tokens(
    user_id: str,
    user_type: str,
    email: EmailStr,
    account_status: AccountStatus,
    db: AsyncSession,
) -> TokenResponse:
    access_token = create_access_token(
        {
            "sub": str(user_id),
            "user_type": user_type,
            "email": email,
            "account_status": account_status,
        }
    )
    refresh_token = await create_refresh_token(
        user_id, email, user_type, account_status, db
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_type=user_type,
        email=email,
        account_status=account_status,
        token_type="bearer",
    )


async def verify_refresh_token(token: str, db: AsyncSession) -> str:
    result = await db.execute(
        "SELECT user_id, expires_at, is_revoked FROM refresh_tokens WHERE token = :token",
        {"token": token},
    )
    row = result.fetchone()

    if not row:
        return None

    user_id, expires_at, is_revoked = row

    if is_revoked or expires_at < datetime.now():
        return None

    return user_id


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    query = select(User).where(User.id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user is None or user.is_blocked:
        raise credentials_exception

    return user


async def get_current_active_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )
    return current_user


async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.user_type != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions for this resource",
        )
    return current_user


async def revoke_refresh_token(token: str, db: AsyncSession) -> bool:
    result = await db.execute(
        "UPDATE refresh_tokens SET is_revoked = TRUE WHERE token = :token RETURNING id",
        {"token": token},
    )
    row = result.fetchone()
    await db.commit()

    return row is not None


async def refresh_access_token(refresh_token: str, db: AsyncSession) -> dict:
    """Create new access and refresh tokens"""
    try:
        # Verify the refresh token
        stmt = (
            select(RefreshToken)
            .where(
                RefreshToken.token == refresh_token,
                RefreshToken.is_revoked == False,
                RefreshToken.expires_at > datetime.now(),
            )
            .options(joinedload(RefreshToken.user))
        )

        result = await db.execute(stmt)
        token = result.scalar_one_or_none()

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        # Create new access token
        access_token = create_access_token(
            data={
                "user_id": str(token.user_id),
                "user_type": token.user.user_type,
                "email": token.user.email,
                "account_status": token.user.account_status,
            }
        )

        # Create new refresh token
        new_refresh_token = str(uuid.uuid4())

        # Save new refresh token
        new_token = RefreshToken(
            token=new_refresh_token,
            user_id=token.user_id,
            user_type=token.user.user_type,
            email=token.user.email,
            account_status=token.user.account_status,
            expires_at=datetime.now() + timedelta(days=7),
        )

        # Revoke old refresh token
        token.is_revoked = True

        db.add(new_token)
        await db.commit()

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
