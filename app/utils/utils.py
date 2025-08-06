from datetime import datetime, timedelta
import secrets
import re
from decimal import Decimal
import json
from uuid import UUID
import uuid
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
from redis import Redis
from exponent_server_sdk_async import (
    AsyncPushClient,
    PushMessage,
    DeviceNotRegisteredError,
    PushServerError,
)
from app.models.models import ChargeAndCommission, User, Profile
from app.schemas.status_schema import UserType, BankSchema
from app.config.config import settings, redis_client
from app.schemas.user_schemas import AccountDetails, AccountDetailResponse
from app.utils.logger_config import setup_logger


flutterwave_base_url = "https://api.flutterwave.com/v3"
servipal_base_url = "https://servipalbackend.onrender.com/api"
banks_url = "https://api.flutterwave.com/v3/banks/NG"

logger = setup_logger()


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


# GET PAYMENT LINK
async def get_product_payment_link(id: UUID, amount: Decimal, current_user: User):
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}
        details = {
            "tx_ref": str(id),
            "amount": str(amount),
            "currency": "NGN",
            "redirect_url": f"{servipal_base_url}/payment/product-payment-callback",
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


async def get_all_banks() -> list[BankSchema]:
    cache_key = "banks_list"
    cached_banks = redis_client.get(cache_key)

    if cached_banks:
        return json.loads(cached_banks)
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(banks_url, headers=headers)
            banks = response.json()["data"]

            sorted_banks = sorted(banks, key=lambda bank: bank["name"])

            redis_client.set(cache_key, json.dumps(sorted_banks, default=str), ex=86400)
            return sorted_banks

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


async def send_sms(phone_number: str, phone_code: str) -> dict:
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
        "sms": f"Your verification code is: {phone_code}. This code will expire in 25 minutes.",
        "type": "plain",
        "channel": "generic",
        "api_key": settings.SMS_API_KEY,
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
    # reference: str,
    beneficiary_name: str,
    charge: ChargeAndCommission,
):
    reference = "ServiPal-" + datetime.now().strftime("%Y%d%m%H%M%S%f")

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
        "narration": f"Transfer of ₦{amount} to {account_number:,.2f} was successful!",
        "currency": "NGN",
        "reference": "{reference",
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


async def resolve_account_details(
    data: AccountDetails,
) -> AccountDetailResponse:
    """
    Resolve bank account details using Flutterwave API

    Args:
        account_number: Bank account number
        account_bank: Bank code (e.g., "044" for Access Bank)

    Returns:
        Dict containing account details in format:
        {
            "account_number": "0690000032",
            "account_name": "Pastor Bright"
        }

    Raises:
        httpx.HTTPStatusError: If the API request fails
        httpx.RequestError: If there's a network error
    """

    payload = {"account_number": data.account_number, "account_bank": data.account_bank}

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {settings.FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{flutterwave_base_url}/accounts/resolve",
                json=payload,
                headers=headers,
            )

            response.raise_for_status()

            # Get the raw response
            raw_response = response.json()

            # Extract and flatten the required fields
            if raw_response.get("status") == "success" and "data" in raw_response:
                data = raw_response["data"]

                formatted_response = {
                    "account_number": data["account_number"],
                    "account_name": data["account_name"],
                }
                return formatted_response

        except httpx.HTTPStatusError as e:
            print(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            print(f"Request error occurred: {e}")
            raise


# async def send_push_message(token, message, extra=None):
#     try:
#         response = AsyncPushClient().publish(
#             PushMessage(to=token, body=message, data=extra)
#         )
#         return response
#     except PushServerError:
#         # Encountered some likely formatting/validation error.
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Something went wrong!",
#         )


async def send_push_notification(
    tokens: list[str], message: str, title: str, extra=None, navigate_to=None
):
    push_client = AsyncPushClient()
    push_messages = [
        PushMessage(
            to=token,
            body=message,
            data={"extra": extra, "navigate_to": navigate_to},
            title=title,
            sound="default",
        )
        for token in tokens
    ]

    try:
        push_tickets = await push_client.publish_multiple(push_messages=push_messages)

        for ticket in push_tickets:
            if ticket.is_success():
                logger.info("Notification sent to: {}", ticket.push_message.to)
            else:
                logger.warn(
                    "Error sending notification to: {}, Error: {}",
                    ticket.push_message.to,
                )
    except PushServerError as e:
        logger.error("Push Server Error: {}", e)
        raise
    except DeviceNotRegisteredError as e:
        logger.error("Device not registered error: {}", e)
        raise


async def get_user_notification_token(db: AsyncSession, user_id):
    result = await db.execute(select(User.notification_token).where(User.id == user_id))
    token = result.scalar_one()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification token missing"
        )
    return token


async def get_full_name_or_business_name(db: AsyncSession, user_id: UUID) -> str:
    result = await db.execute(select(Profile).where(Profile.user_id == user_id))
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )

    name = profile.full_name or profile.business_name
    if not name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User name or business name not found in profile",
        )
    return name
