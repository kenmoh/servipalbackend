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
from app.models.models import ChargeAndCommission, User
from app.schemas.status_schema import UserType
from app.config.config import settings, redis_client


flutterwave_base_url = "https://api.flutterwave.com/v3"
servipal_base_url = "https://servipalbackend.onrender.com/api"
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
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to verify transaction reference: {str(e)}"
        )
        


# GET PAYMENT LINK
async def get_payment_link(id: UUID, amount: Decimal, current_user: User):
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}
        details = {
            "tx_ref": str(id),
            "amount": str(amount),
            "currency": "NGN",
            "redirect_url": f"{servipal_base_url}/payment/order-payment-callback",
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
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {str(e)}")
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
            "redirect_url": f"{servipal_base_url}/payment/fund-wallet-callback",
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
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {str(e)}")
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
            detail="Password must be at least 8 characters long",
        )

    if not re.search(r"[A-Z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one uppercase letter",
        )

    if not re.search(r"[a-z]", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one lowercase letter",
        )

    if not re.search(r"\d", password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one number",
        )

    if not re.search(r"[ !@#$%&'()*+,-./[\\\]^_`{|}~" + r'"]', password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must contain at least one special character",
        )

    return True


# <<<<< ---------- Account lockout after failed attempts:


def check_login_attempts(email: str, redis_client: Redis) -> None:
    """Check and handle failed login attempts"""
    key = f"login_attempts:{email}"
    attempts = redis_client.get(key)

    if attempts and int(attempts) >= 5:
        # Lock account for 15 minutes after 5 failed attempts
        if not redis_client.get(f"account_locked:{email}"):
            redis_client.setex(f"account_locked:{email}", 900, 1)  # 15 minutes
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account temporarily locked. Please try again later.",
        )


def record_failed_attempt(email: str, redis_client: Redis) -> None:
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
    return datetime.now() + timedelta(hours=hours)


async def send_sms(phone_number: str, message: str) -> dict:
    """
    Send SMS using Termii API with httpx async client

    Args:
        phone_number: Recipient's phone number
        message: SMS content

    Returns:
        dict: API response
    """
    api_key = settings.SMS_API_KEY
    termii_url = "https://api.ng.termii.com/api/sms/send"

    payload = {
        "to": phone_number,
        "from": "ServiPal",
        "sms": message,
        "type": "plain",
        "channel": "generic",
        "api_key": api_key,
    }

    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(termii_url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SMS gateway error: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send SMS: {str(e)}",
        )


async def get_bank_code(bank_name: str):
    headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(banks_url, headers=headers)
            data = response.json()["data"]
            for bank in data:
                if bank.get("name") == bank_name:
                    return bank["code"]
                return None

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Payment gateway error: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve banl code: {str(e)}",
        )


async def transfer_money_to_user_account(
    bank_code: str,
    account_number: str,
    amount: str,
    narration: str,
    reference: str,
    beneficiary_name: str,
    charge: ChargeAndCommission,
):
    headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}
    payload = {
        "account_bank": bank_code,
        "account_number": account_number,
        # "amount": amount,
        "amount": (
            f"{Decimal(amount) - (charge.payout_charge_transaction_upto_5000_naira * charge.value_added_tax + charge.payout_charge_transaction_upto_5000_naira)}"
            if Decimal(amount) <= Decimal(5000)
            else (
                f"{Decimal(amount) - (charge.payout_charge_transaction_from_5001_to_50_000_naira * charge.value_added_tax + charge.payout_charge_transaction_from_5001_to_50_000_naira)}"
                if Decimal(amount) <= Decimal(50000)
                else (
                    f"{Decimal(amount) - (charge.payout_charge_transaction_above_50_000_naira * charge.value_added_tax + charge.payout_charge_transaction_above_50_000_naira)}"
                )
            )
        ),
        "narration": narration,
        "currency": "NGN",
        "reference": reference,
        "callback_url": f"{servipal_base_url}/withdrawals/callback",
        "debit_currency": "NGN",
        "beneficiary_name": beneficiary_name,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{flutterwave_base_url}/transfers", json=payload, headers=headers
            )

            response_data = response.json()

            if response_data.get("status") == "success":
                return response_data

            else:
                retry_response = client.post(
                    f"{flutterwave_base_url}/transfers/{response_data.get('data')['id']}/retries",
                    json=payload,
                    headers=headers,
                )
                if retry_response.json().get("status") == "success":
                    return response_data
                else:
                    return {
                        "status": "failed",
                        "message": retry_response.json().get("message"),
                        "amount": retry_response.json().get("data").get("amount"),
                        "created_at": retry_response.json()
                        .get("data")
                        .get("created_at"),
                    }

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Payment gateway error: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to withdraw: {str(e)}",
        )


# async def send_welcome_email(subject: str, email_to: EmailStr, body: dict, temp_name: str):
#     message = MessageSchema(
#         subject=subject,
#         recipients=[email_to],
#         template_body=body,
#         subtype="html",
#     )

#     mail = FastMail(connection_config)
#     await mail.send_message(message=message, template_name=temp_name)
#     return JSONResponse(status_code=200, content={"message": "email has been sent"})
