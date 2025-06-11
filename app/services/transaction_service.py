import asyncio
from typing import Dict, Any
import datetime
from uuid import UUID
import logging
from fastapi import BackgroundTasks, HTTPException, Request, status
import httpx
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pydantic import EmailStr

from app.models.models import (
    ChargeAndCommission,
    Order,
    User,
    Wallet,
    Transaction,
    OrderItem,
    Delivery
)
from app.schemas.marketplace_schemas import (
    TopUpRequestSchema,
    TransferDetailResponseSchema,
    BankCode,
    WithdrawalShema,
)
from app.schemas.order_schema import OrderResponseSchema
from app.schemas.status_schema import PaymentStatus, TransactionType
from app.utils.logger_config import setup_logger
from app.utils.utils import (
    get_bank_code,
    get_fund_wallet_payment_link,
    transfer_money_to_user_account,
    verify_transaction_tx_ref,
    flutterwave_base_url,
)
from app.config.config import settings, redis_client

logger = setup_logger()


async def get_wallet(wallet_id, db: AsyncSession):
    stmt = select(Wallet).where(Wallet.id == wallet_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_database(order, db: AsyncSession):
    for attempt in range(3):
        try:
            order.payment_status = PaymentStatus.PAID
            await db.commit()
            await db.refresh(order)
            break
        except Exception as e:
            logger.error(f"Error updating database: {e}")
            await asyncio.sleep(1)


async def top_up_wallet(
    db: AsyncSession, current_user: User, topup_data: TopUpRequestSchema
) -> TopUpRequestSchema:
    """
    Initiates a wallet top-up transaction with improved error handling and efficiency.

    Args:
        db: The database session.
        current_user: The user initiating the top-up.
        topup_data: The top-up request data (amount).

    Returns:
        Details of the initiated transaction, including the payment link.

    Raises:
        HTTPException: If wallet creation fails or payment link generation fails.
    """
    try:
        # Get or create wallet in a single operation
        wallet = await db.get(Wallet, current_user.id)

        if not wallet:
            wallet = Wallet(
                id=current_user.id,
                balance=0,
                escrow_balance=0,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(wallet)

        if wallet.balance >= 1_00_000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Wallet balance cannot be more than NGN 100000",
            )

        # Create the transaction record
        transaction = Transaction(
            wallet_id=wallet.id,
            amount=topup_data.amount,
            transaction_type=TransactionType.CREDIT,
            status=PaymentStatus.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(transaction)

        # Fush to get the transaction ID for the payment link
        await db.flush()

        # Generate payment link
        payment_link = await get_fund_wallet_payment_link(
            id=transaction.id, amount=transaction.amount, current_user=current_user
        )

        # Update transaction with payment link
        transaction.payment_link = payment_link

        refreshed_transaction = await db.get(Transaction, transaction.id)

        return TopUpRequestSchema.model_validate(refreshed_transaction)

    except Exception as e:
        # Log error for debugging
        logging.error(f"Failed to top up wallet: {str(e)}", exc_info=True)

        # Map different error types to appropriate HTTP responses
        if "wallet" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Failed to create or access wallet: {str(e)}",
            )
        elif "payment link" in str(e).lower() or "fund" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Payment gateway error: {str(e)}",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process wallet top-up: {str(e)}",
            )


# --- Webhook Handler ---

# SUCCESS WEBHOOK

# async def handle_payment_webhook(
#     request: Request,
#     background_task: BackgroundTasks,
#     db: AsyncSession,
# ):
#     """Handle webhooks for both normal payments and transfers"""

#     # Validate webhook signature
#     signature = request.headers.get("verif-hash")
#     if signature is None or signature != settings.FLW_SECRET_HASH:
#         raise HTTPException(status_code=401, detail="Unauthorized")

#     # Get payload
#     payload = await request.json()

#     try:
#         # Determine webhook type and extract transaction reference
#         # Transfer webhooks have 'event.type' field
#         is_transfer = 'event.type' in payload

#         if is_transfer:
#             # Handle transfer webhook (has 'event.type' field)
#             tx_ref = payload.get('txRef')
#             if not tx_ref:
#                 logging.error(f"Missing txRef in transfer webhook payload: {payload}")
#                 raise HTTPException(status_code=400, detail="Invalid transfer payload format")

#             # Validate transfer-specific required fields
#             # For bank transfer webhooks, the data is at root level, not nested under 'data'
#             required_fields = ["status", "amount", "currency", "txRef"]
#             if not all(field in payload for field in required_fields):
#                 logging.error(f"Missing required fields in transfer webhook payload: {payload}")
#                 raise HTTPException(status_code=400, detail="Invalid transfer payload format")

#             status_value = payload.get("status")
#             amount_value = payload.get("amount")
#             currency_value = payload.get("currency")


#         else:
#             # Handle normal payment webhook (no 'event.type' field)
#             tx_ref = payload.get("txRef")
#             if not tx_ref:
#                 logging.error(f"Missing txRef in payment webhook payload: {payload}")
#                 raise HTTPException(status_code=400, detail="Invalid payment payload format")

#             # Validate payment-specific required fields
#             required_fields = ["status",  "amount", "currency"]
#             if not all(field in payload for field in required_fields):
#                 logging.error(f"Missing required fields in payment webhook payload: {payload}")
#                 raise HTTPException(status_code=400, detail="Invalid payment payload format")

#             status_value = payload.get("status")
#             amount_value = payload.get("amount")
#             currency_value = payload.get("currency")

#         # Convert tx_ref to UUID if possible
#         try:
#             order_id = UUID(tx_ref)
#         except (ValueError, TypeError):
#             order_id = tx_ref

#         # Get order from database
#         stmt = select(Order).where(Order.id == order_id)
#         result = await db.execute(stmt)
#         db_order = result.scalar_one_or_none()

#         if not db_order:
#             logging.warning(f"Order not found for txRef: {tx_ref}")
#             return {"message": "Order not found"}

#         # Validate payment/transfer details
#         is_valid_payment = (
#             status_value == "successful"
#             and amount_value == db_order.total_price
#             and currency_value == "NGN"
#             and db_order.payment_status != PaymentStatus.PAID
#         )

#         # For normal payments, also check total_price
#         # if not is_transfer and payload.get("total_price") != db_order.total_price:
#         #     is_valid_payment = False

#         if is_valid_payment:
#             # Verify transaction with payment provider
#             verify_result = await verify_transaction_tx_ref(tx_ref)
#             if verify_result.get("data", {}).get("status") == "successful":
#                 # Update the database in the background with retry mechanism
#                 background_task.add_task(update_database, db_order, db)
#                 logging.info(f"{'Transfer' if is_transfer else 'Payment'} webhook processed successfully for txRef: {tx_ref}")
#                 return {"message": "Success"}
#             else:
#                 logging.warning(f"Transaction verification failed for txRef: {tx_ref}")
#                 return {"message": "Transaction verification failed"}
#         else:
#             logging.warning(f"Payment validation failed for txRef: {tx_ref}. Status: {status_value}, Amount: {amount_value}, Currency: {currency_value}")
#             return {"message": "Payment validation failed"}

#     except HTTPException:
#         # Re-raise HTTP exceptions
#         raise
#     except Exception as e:
#         logging.error(f"Error processing webhook: {e}", exc_info=True)
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="INTERNAL_SERVER_ERROR",
#         )


async def handle_payment_webhook(
    request: Request,
    background_task: BackgroundTasks,
    db: AsyncSession,
):
    # Validate webhook signature
    signature = request.headers.get("verif-hash")
    if signature is None or signature != settings.FLW_SECRET_HASH:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Get payload
    payload = await request.json()
    tx_ref = payload.get("txRef")

    # Validate required payload fields
    required_fields = ["status", "total_price", "amount", "currency", "txRef"]
    if not all(field in payload for field in required_fields):
        logging.error(f"Missing required fields in webhook payload: {payload}")
        raise HTTPException(status_code=400, detail="Invalid payload format")

    try:
        # Convert string to UUID
        try:
            order_id = UUID(tx_ref)
        except (ValueError, TypeError):
            order_id = tx_ref

        # Get order
        stmt = select(Order).where(Order.id == order_id)
        result = await db.execute(stmt)
        db_order = result.scalar_one_or_none()

        if not db_order:
            logging.warning(f"Order not found for txRef: {tx_ref}")
            return {"message": "Order not found"}

        # Check if the payment is valid and not already processed
        if (
            payload["status"] == "successful"
            and payload["total_price"] == db_order.total_price
            and payload["amount"] == db_order.total_price
            and payload["currency"] == "NGN"
            and db_order.payment_status != PaymentStatus.PAID
        ):
            verify_result = await verify_transaction_tx_ref(tx_ref)
            if verify_result.get("data", {}).get("status") == "successful":
                # Update the database in the background with retry mechanism
                background_task.add_task(update_database, db_order, db)
                return {"message": "Success"}

        return {"message": "Payment validation failed"}

    except Exception as e:
        logging.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INTERNAL_SERVER_ERROR",
        )


async def fund_wallet_callback(request: Request, db: AsyncSession):
    # Get query parameters without awaiting them (they're not coroutines)
    tx_ref = request.query_params["tx_ref"]
    tx_status = request.query_params["status"]

    # First get the transaction
    stmt = select(Transaction).where(Transaction.id == tx_ref)
    result = await db.execute(stmt)
    transaction = result.scalar_one_or_none()

    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Verify payment status
    if tx_status == "successful":
        # Verify with payment gateway
        verify_result = await verify_transaction_tx_ref(tx_ref)
        if verify_result.get("data", {}).get("status") == "successful":
            # Get charge configuration (using proper query)
            charge_stmt = select(ChargeAndCommission)
            charge_result = await db.execute(charge_stmt)
            charge = charge_result.scalar_one_or_none()

            if not charge:
                raise HTTPException(
                    status_code=500, detail="Charge configuration not found"
                )

            # Get wallet
            wallet = await get_wallet(transaction.wallet_id, db)

            # Calculate the amount to add
            amount_to_add = calculate_net_amount(transaction.amount, charge)

            # Update wallet balance
            wallet.balance += amount_to_add

            # Update transaction status
            transaction.status = PaymentStatus.PAID

            # Commit changes
            await db.commit()
            await db.refresh(transaction)
            await db.refresh(wallet)

            return {
                "payment_status": transaction.status,
                "wallet_balance": wallet.balance,
                "amount_added": amount_to_add,
            }

    elif tx_status == "cancelled":
        transaction.status = PaymentStatus.CANCELLED
    else:
        transaction.status = PaymentStatus.FAILED

    # Save status changes for non-successful transactions
    await db.commit()
    return {"payment_status": transaction.status}


# Helper function to calculate the net amount after charges
def calculate_net_amount(amount: float, charge: ChargeAndCommission) -> float:
    """Calculate the net amount after deducting transaction charges."""
    if amount <= 5000:
        charge_fee = charge.payout_charge_transaction_upto_5000_naira
    elif amount <= 50000:
        charge_fee = charge.payout_charge_transaction_from_5001_to_50_000_naira
    else:
        charge_fee = charge.payout_charge_transaction_above_50_000_naira

    # Calculate total charge (fee + VAT)
    total_charge = charge_fee + (charge_fee * charge.value_added_tax)

    # Return amount after deducting charges
    return amount - total_charge


async def order_payment_callback(request: Request, db: AsyncSession):
    tx_ref = request.query_params["tx_ref"]
    tx_status = request.query_params["status"]

    # Ccheck if the transaction was successful
    verify_tranx = await verify_transaction_tx_ref(tx_ref)

    # Determine the appropriate payment status
    if (
        tx_status == "successful"
        and verify_tranx.get("data", {}).get("status") == "successful"
    ):
        new_status = PaymentStatus.PAID

    elif tx_status == "cancelled":
        new_status = PaymentStatus.CANCELLED
    else:
        new_status = PaymentStatus.FAILED

    # Update only the payment status and return it
    stmt = (
        update(Order)
        .where(Order.id == UUID(tx_ref))
        .values(order_payment_status=new_status)
        .returning(Order.order_payment_status, Order.owner_id, Order.total_price)
    )

    result = await db.execute(stmt)
    await db.commit()

    wallet_result = await db.execute(select(Wallet.escrow_balance).where(Wallet.id == result.owner_id))
    escrow_balance = wallet_result.scalar_one_or_none()

    if tx_status == 'successful':
        # Uodate vendour escrow
        await db.execute(
            update(Wallet)
            .where(Wallet.id == result.owner_id)
            .values({"escrow_balance": escrow_balance + result.total_price})
        )
        await db.commit()



    # Get the updated status directly
    updated_status = result.scalar_one()

    delivery_stmt = select(Delivery.id).where(Delivery.order_id == UUID(tx_ref))
    delivery_result = await db.execute(delivery_stmt)
    delivery_id = delivery_result.scalar_one_or_none()

    if delivery_id:
        redis_client.delete(f"delivery:{delivery_id}")
    redis_client.delete("deliveries")

    return {"order_payment_status": updated_status}


async def pay_with_wallet(
    db: AsyncSession,
    order_id: UUID,
    buyer: User,
) -> PaymentStatus:
    """
    Handles pay with wallet.

    Args:
        db: The database session.
        order_id: The ID of the order to pay.
        buyer: The authenticated user making the purchase.

    Returns:
        The completed order with updated payment status.

    Raises:
        HTTPException: Various exceptions for validation errors.
    """
    # Fetch order with related items and vendor in one efficient query
    stmt_order = (
        select(Order)
        .where(
            Order.id == order_id,
            Order.owner_id == buyer.id,
            Order.order_payment_status == PaymentStatus.PENDING,
        )
        .options(
            selectinload(Order.order_items).selectinload(OrderItem.item),
            selectinload(Order.vendor),
        )
        .with_for_update()
    )
    result_order = await db.execute(stmt_order)
    order = result_order.scalar_one_or_none()

    if not check_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    # Fetch both wallets in parallel for better performance
    wallets_stmt = (
        select(Wallet)
        .where(Wallet.id.in_([buyer.id, order.vendor_id]))
        .with_for_update()
    )
    result = await db.execute(wallets_stmt)
    wallets = result.scalars().all()

    # Map wallets by ID for easy access
    wallets_by_id = {wallet.id: wallet for wallet in wallets}
    buyer_wallet = wallets_by_id.get(buyer.id)
    seller_wallet = wallets_by_id.get(order.vendor_id)

    # Validate wallets
    if not buyer_wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Buyer wallet not found"
        )
    if not seller_wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Seller wallet not found"
        )

    # Check if buyer has enough funds
    if buyer_wallet.balance < order.total_price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient funds in wallet",
        )

    # Process the payment
    buyer_wallet.balance -= order.total_price
    seller_wallet.escrow_balance += order.total_price

    # Create transactions in bulk
    current_time = datetime.utcnow()
    transaction_values = [
        # Buyer transaction (DEBIT)
        {
            "wallet_id": buyer_wallet.id,
            "amount": order.total_price,
            "transaction_type": TransactionType.DEBIT,
            "status": PaymentStatus.PAID,
            "created_at": current_time,
            "updated_at": current_time,
        },
        # Seller transaction (CREDIT)
        {
            "wallet_id": seller_wallet.id,
            "amount": order.total_price,
            "transaction_type": TransactionType.CREDIT,
            "status": PaymentStatus.PAID,
            "created_at": current_time,
            "updated_at": current_time,
        },
    ]

    await db.execute(insert(Transaction), transaction_values)

    # Update order status
    order.order_payment_status = PaymentStatus.PAID

    await db.refresh(order)

    return order.payment_status


async def initiate_bank_transfer(
    current_user: User, order_id: UUID, db: AsyncSession
) -> TransferDetailResponseSchema:
    """
    Initiate a bank transfer charge using Flutterwave API

    Args:
        amount: Amount to charge
        email: Customer email
        currency: Currency code (default: NGN)
        tx_ref: Transaction reference (auto-generated if not provided)

    Returns:
        Dict containing the API response

    Raises:
        httpx.HTTPStatusError: If the API request fails
        httpx.RequestError: If there's a network error
    """

    result = await db.execute(select(Order).where(Order.id == order_id))

    order = result.scalar_one_or_none()

    payload = {
        "amount": str(order.total_price),
        "email": current_user.email,
        "currency": "NGN",
        "tx_ref": str(order.id),
    }

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {settings.FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{flutterwave_base_url}/charges?type=bank_transfer",
                json=payload,
                headers=headers,
            )

            result = response.json()
            message = result["message"]

            if result["status"] != "success":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=f"{message}"
                )
            auth_data = result["meta"]["authorization"]

            return {
                "status": result["status"],
                "message": result["message"],
                "transfer_reference": auth_data["transfer_reference"],
                "transfer_account": auth_data["transfer_account"],
                "transfer_bank": auth_data["transfer_bank"],
                "account_expiration": auth_data["account_expiration"],
                "transfer_note": auth_data["transfer_note"],
                "transfer_amount": auth_data["transfer_amount"],
                "mode": auth_data["mode"],
            }

        except httpx.HTTPStatusError as e:
            print(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            print(f"Request error occurred: {e}")
            raise


async def make_withdrawal(
    db: AsyncSession, current_user: User, bank_code: BankCode
) -> WithdrawalShema:
    """Process withdrawal of entire wallet balance"""

    # Load user with profile and wallet in a single query
    stmt = (
        select(User)
        .options(selectinload(User.profile), selectinload(User.wallet))
        .where(User.id == current_user.id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    user = result.unique().scalar_one_or_none()

    result = await db.execute(select(ChargeAndCommission))

    charge = await result.scalars().fetchone()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user.is_bloccked or user.rider_is_suspended_for_order_cancel:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your account is suspended. Please contact support.",
        )

    if (
        not user.profile
        or not user.profile.bank_account_number
        or not user.profile.bank_name
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please update your profile with bank account details",
        )

    if not user.wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found"
        )

    if user.wallet.balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient funds in wallet",
        )

    try:
        # Create withdrawal transaction
        withdrawal = Transaction(
            user_id=user.id,
            wallet_id=user.wallet.id,
            amount=user.wallet.balance,
            payment_status=PaymentStatus.PENDING,
            transaction_type=TransactionType.DEBIT,
        )
        db.add(withdrawal)
        await db.flush()

        if withdrawal.amount > user.wallet.balance:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient funds in wallet",
            )

        transfer_response = await transfer_money_to_user_account(
            bank_code=bank_code.bank_code,
            amount=str(user.wallet.balance),
            # narration=f"Wallet withdrawal of ₦ {previous_balance:,.2f}",
            # reference=str(withdrawal.id),
            account_number=user.profile.bank_account_number,
            beneficiary_name=user.profile.account_holder_name,
            charge=charge,
        )

        if transfer_response.get("status") == "success":
            # Update wallet balance
            user.wallet.balance -= withdrawal.amount
            withdrawal.payment_status = PaymentStatus.PAID

            await db.commit()

            return {
                "status": "success",
                "message": "Withdrawal processed successfully",
                "transaction_id": withdrawal.id,
                "amount": user.wallet.balance,
                "bank_name": user.profile.bank_name,
                "account_number": user.profile.bank_account_number,
                "beneficiary": user.profile.account_holder_name,
                "timestamp": withdrawal.created_at,
            }
        elif transfer_response.get("status") in ["cancel", "failed"]:
            user.wallet.balance += withdrawal.amount
            await db.commit()

            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Bank transfer failed: {transfer_response.get('message', 'Unknown error occured')}",
            )

    except Exception as e:
        await db.rollback()
        logger.error(f"Withdrawal failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process withdrawal",
        )
