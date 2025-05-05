from datetime import datetime, timedelta
import secrets
import re
from decimal import Decimal
import json
from uuid import UUID
import uuid
from fastapi import HTTPException, status
import httpx
from redis import Redis
from app.models.models import User
from app.schemas.status_schema import UserType
from app.config.config import settings, redis_client


flutterwave_base_url = "https://api.flutterwave.com/v3"
quick_pickup_base_url = "https://quickpickup.onrender.com/api"
banks_url = "https://api.flutterwave.com/v3/banks/NG"


def unique_id(id: uuid.UUID) -> str:
    return str(id).replace("-", "")


def get_dispatch_id(current_user: User):
    if current_user.user_type == UserType.RIDER:
        return current_user.dispatcher_id
    elif current_user.user_type == UserType.DISPATCH:
        return current_user.id


# Verify Transaction Ref


async def verify_transaction_tx_ref(tx_ref: str):
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{flutterwave_base_url}/transactions/verify_by_reference?tx_ref={tx_ref}",
                headers=headers,
            )

            response_data = response.json()
            return response_data

        return link
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502, detail=f"Payment gateway error: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to verify transaction reference: {str(e)}"
        )


# GET PAYMENT LINK
async def get_payment_link(id: UUID, amount: Decimal, current_user: User):
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}
        details = {
            "tx_ref": unique_id(id),
            "amount": str(amount),
            "currency": "NGN",
            "redirect_url": f"{quick_pickup_base_url}/payment/order-payment-callback",
            "customer": {
                "email": current_user.email,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{flutterwave_base_url}/payments", json=details, headers=headers
            )
            response.raise_for_status()
            response_data = response.json()
            return response_data["data"]["link"]

        return link
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502, detail=f"Payment gateway error: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate payment link: {str(e)}"
        )


async def get_fund_wallet_payment_link(id: UUID, amount: Decimal, current_user: User):
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}
        details = {
            "tx_ref": unique_id(id),
            "amount": str(amount),
            "currency": "NGN",
            "redirect_url": f"{quick_pickup_base_url}/payment/fund-wallet-callback",
            "customer": {
                "email": current_user.email,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{flutterwave_base_url}/payments", json=details, headers=headers
            )
            response.raise_for_status()
            response_data = response.json()
            return response_data["data"]["link"]

        return link
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502, detail=f"Payment gateway error: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate payment link: {str(e)}"
        )


async def get_all_banks():
    cache_key = "banks_list"
    cached_banks = redis_client.get(cache_key)

    if cached_banks:
        print("Cache hit for banks list")
        return json.loads(cached_banks)
    banks = []
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(banks_url, headers=headers)
            data = response.json()["data"]

        for bank in data:
            banks.append(bank["name"])

        banks.sort()
        redis_client.set(cache_key, json.dumps(banks, default=str), ex=86400)
        return banks

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to get banks: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get banks: {str(e)}",
        )


# <<<< ---------- PASSWORD VALIDATION ---------- >>>>>


def validate_password(password: str) -> bool:
    """
    Validate password strength
    Must have:
    - At least 8 characters
    - 1 uppercase letter
    - 1 lowercase letter
    - 1 number
    - 1 special character
    """
    if len(password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters long"
        )

    if not re.search(r"[A-Z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one uppercase letter"
        )

    if not re.search(r"[a-z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one lowercase letter"
        )

    if not re.search(r"\d", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one number"
        )

    if not re.search(r"[ !@#$%&'()*+,-./[\\\]^_`{|}~"+r'"]', password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one special character"
        )

    return True

# <<<<< ---------- Account lockout after failed attempts:


async def check_login_attempts(email: str, redis_client: Redis) -> None:
    """Check and handle failed login attempts"""
    key = f"login_attempts:{email}"
    attempts = redis_client.get(key)

    if attempts and int(attempts) >= 5:
        # Lock account for 15 minutes after 5 failed attempts
        if not redis_client.get(f"account_locked:{email}"):
            redis_client.setex(f"account_locked:{email}", 900, 1)  # 15 minutes
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account temporarily locked. Please try again later."
        )


async def record_failed_attempt(email: str, redis_client: Redis) -> None:
    """Record failed login attempt"""
    key = f"login_attempts:{email}"
    redis_client.incr(key)
    redis_client.expire(key, 900)  # Reset after 15 minutes


# <<<<< ---------- utility for secure token generation:


def generate_secure_token(length: int = 32) -> str:
    """Generate cryptographically secure token"""
    return secrets.token_urlsafe(length)


def generate_expiry(hours: int = 24) -> datetime:
    """Generate token expiry timestamp"""
    return datetime.utcnow() + timedelta(hours=hours)
