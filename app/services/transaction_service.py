import asyncio
from datetime import datetime
from decimal import Decimal
from uuid import UUID
import logging
from fastapi import BackgroundTasks, HTTPException, Request, status
import httpx
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import hmac

from app.models.models import (
    ChargeAndCommission,
    Order,
    User,
    Wallet,
    Transaction,
    OrderItem,
)
from app.schemas.marketplace_schemas import (
    TopUpRequestSchema,
    TopUpResponseSchema,
    TransferDetailResponseSchema,
    BankCode,
    WithdrawalShema,
)
from app.schemas.order_schema import OrderResponseSchema
from app.schemas.status_schema import PaymentMethod, PaymentStatus, RequireDeliverySchema, TransactionType
from app.utils.logger_config import setup_logger
from app.utils.utils import (
    get_bank_code,
    get_fund_wallet_payment_link,
    transfer_money_to_user_account,
    verify_transaction_tx_ref,
    flutterwave_base_url,
    send_push_notification,
    get_user_notification_token,
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
) -> TopUpResponseSchema:
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
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            db.add(wallet)

        if wallet.balance >= 100_000 or (topup_data.amount + wallet.balance) > 100_000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Wallet balance cannot be more than NGN 100, 000",
            )

        # Create the transaction record
        transaction = Transaction(
            wallet_id=wallet.id,
            amount=topup_data.amount,
            transaction_type=TransactionType.CREDIT,
            payment_status=PaymentStatus.PENDING,
            payment_by=current_user.profile.full_name
            or current_user.profile.business_name,
            created_at=datetime.now(),
            updated_at=datetime.now(),
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
        await db.commit()
        await db.refresh(transaction)

        return transaction

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


# async def handle_charge_completed_callback(request: Request, db: AsyncSession, payload=None):
#     if payload is None:
#         payload = await request.json()
#     event = payload.get("event")
#     data = payload.get("data", {})

#     if event == "charge.completed" and data.get("status") == "successful":
#         payment_type = data.get("payment_type") or data.get("paymentType")
#         tx_ref = data.get("tx_ref") or data.get("txRef") or data.get("reference")
#         amount_paid = data.get("amount")
#         currency = data.get("currency")

#         order = None
#         if tx_ref:
#             result = await db.execute(select(Order).where(Order.id == UUID(tx_ref)))
#             order = result.scalar_one_or_none()

#         if not order:
#             return {"status": "ignored", "reason": "Order not found"}

#         if order.order_payment_status == PaymentStatus.PAID:
#             return {"status": "ignored", "reason": "Order already paid"}

#         if payment_type == "bank_transfer":
#             # Bank transfer-specific logic
#             await db.execute(update(Transaction).where(Transaction.id == UUID(tx_ref)).values(payment_method=PaymentMethod.BANK_TRANSFER))
#             db.commit()

#         elif payment_type == "card":
#             # Card payment-specific logic
#             await db.execute(update(Transaction).where(Transaction.id == UUID(tx_ref)).values(payment_method=PaymentMethod.CARD))
#             db.commit()

#         # Mark order as paid
#         order.order_payment_status = PaymentStatus.PAID
#         await db.commit()

#         # Send notifications to buyer and seller
#         buyer_token = await get_user_notification_token(db=db, user_id=order.owner_id)
#         seller_token = await get_user_notification_token(db=db, user_id=order.vendor_id)
#         amount_str = f"₦{amount_paid}" if currency == "NGN" else f"{amount_paid} {currency}"
#         if buyer_token:
#             await send_push_notification(
#                 tokens=[buyer_token],
#                 title="Payment Successful",
#                 message=f"Your payment of {amount_str} was successful.",
#             )
#         if seller_token:
#             await send_push_notification(
#                 tokens=[seller_token],
#                 title="Order Paid",
#                 message=f"You have received a new order payment of {amount_str}.",
#             )

#         return {"status": "success", "order_id": str(order.id), "payment_type": payment_type}

#     return {"status": "ignored", "reason": "Not a successful charge.completed event"}


async def handle_charge_completed_callback(
    request: Request, db: AsyncSession, payload=None
):
    if payload is None:
        payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})

    if event == "charge.completed" and data.get("status") == "successful":
        payment_type = data.get("payment_type") or data.get("paymentType")
        tx_ref = data.get("tx_ref") or data.get("txRef") or data.get("reference")
        amount_paid = data.get("amount")
        currency = data.get("currency")

        # Try to get Order first
        order = None
        if tx_ref:
            result = await db.execute(select(Order).where(Order.id == UUID(tx_ref)))
            order = result.scalar_one_or_none()

        if order:
            # --- Handle Order Payment ---
            if order.order_payment_status == PaymentStatus.PAID:
                await db.execute(
                    update(Transaction)
                    .where(Transaction.id == order.id)
                    .values(payment_method=payment_type)
                )
                await db.commit()
                return {"status": "ignored", "reason": "Order already paid"}

            # Mark order as paid
            order.order_payment_status = PaymentStatus.PAID
            await db.execute(
                update(Transaction)
                .where(Transaction.id == order.id)
                .values(payment_method=payment_type)
            )
            await db.commit()

            # Send notifications
            buyer_token = await get_user_notification_token(
                db=db, user_id=order.owner_id
            )
            seller_token = await get_user_notification_token(
                db=db, user_id=order.vendor_id
            )
            amount_str = (
                f"₦{amount_paid}" if currency == "NGN" else f"{amount_paid} {currency}"
            )
            if buyer_token:
                await send_push_notification(
                    tokens=[buyer_token],
                    title="Payment Successful",
                    message=f"Your payment of {amount_str} was successful.",
                )
            if seller_token:
                await send_push_notification(
                    tokens=[seller_token],
                    title="Order Paid",
                    message=f"You have received a new order payment of {amount_str}.",
                )

            return {
                "status": "success",
                "order_id": str(order.id),
                "payment_type": payment_type,
            }

        # --- If not an order, try as a wallet top-up transaction ---
        transaction = None
        if tx_ref:
            result = await db.execute(
                select(Transaction).where(Transaction.id == UUID(tx_ref))
            )
            transaction = result.scalar_one_or_none()

        if transaction:
            if transaction.payment_status == PaymentStatus.PAID:
                transaction.payment_method = payment_type
                await db.commit()

            # Mark transaction as paid
            transaction.payment_status = PaymentStatus.PAID
            transaction.payment_method = payment_type

            # Update wallet balance
            wallet = await db.get(Wallet, transaction.wallet_id)
            if wallet:
                # Get charge config
                charge_stmt = select(ChargeAndCommission)
                charge_result = await db.execute(charge_stmt)
                charge = charge_result.scalar_one_or_none()
                amount_to_add = calculate_net_amount(transaction.amount, charge)
                wallet.balance += amount_to_add

            await db.commit()

            # Notify user
            token = await get_user_notification_token(db=db, user_id=wallet.id)
            if token:
                await send_push_notification(
                    tokens=[token],
                    title="Wallet Top-up",
                    message=f"Your wallet top-up of ₦{amount_paid} was successful.",
                )

            return {
                "status": "success",
                "transaction_id": str(transaction.id),
                "payment_type": payment_type,
            }

        # If neither order nor transaction found
        return {"status": "ignored", "reason": "Order/Transaction not found"}

    return {"status": "ignored", "reason": "Not a successful charge.completed event"}


async def handle_payment_webhook(request: Request, db: AsyncSession):
    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})

    # verify webhook signature
    secret_hash = settings.FLW_SECRET_HASH
    signature = request.headers.get("verif-hash")
    if not hmac.compare_digest(signature, secret_hash):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if event == "charge.completed":
        return await handle_charge_completed_callback(request, db, payload=payload)
    elif event == "transfer.completed":
        # Handle payout/withdrawal webhook (not implemented)
        return {"status": "ignored", "reason": "Not implemented"}
    # Add more event types as needed

    return {"status": "ignored", "reason": "Unknown event"}


async def handle_payment_webhook_old(
    request: Request,
    background_task: BackgroundTasks,
    db: AsyncSession,
):
    # Get payload
    payload = await request.json()
    tx_ref = payload.get("txRef")

    # Validate webhook signature
    signature = request.headers.get("verif-hash")
    if signature is None or signature != settings.FLW_SECRET_HASH:
        raise HTTPException(status_code=401, detail="Unauthorized")

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

        owner_token = await get_user_notification_token(
            db=db, user_id=db_order.owner_id
        )

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

                if owner_token:
                    await send_push_notification(
                        tokens=[owner_token],
                        title="Payment Received",
                        message=f"Your payment of ₦{db_order.total_price} has been received.",
                    )
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
    stmt = select(Transaction).where(Transaction.id == UUID(tx_ref))
    result = await db.execute(stmt)
    transaction = result.scalar_one_or_none()

    new_status = None

    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")


    verify_tranx = await verify_transaction_tx_ref(tx_ref)
    if verify_tranx is None:
        logging.error(f"verify_transaction_tx_ref returned None for tx_ref: {tx_ref}")
        raise HTTPException(status_code=502, detail="Failed to verify transaction status")

    # Defensive: ensure verify_tranx is a dict and has 'data'
    verify_data = verify_tranx.get("data") if isinstance(verify_tranx, dict) else None
    verify_status = verify_data.get("status") if verify_data else None

    if tx_status == "successful" and verify_status == "successful":
        new_status = PaymentStatus.PAID
    elif tx_status == "cancelled":
        new_status = PaymentStatus.CANCELLED
    else:
        new_status = PaymentStatus.FAILED

    try:
        # Update transaction status
        await db.execute(
            update(Transaction)
            .where(Transaction.id == UUID(tx_ref))
            .values(payment_status=new_status)
        )

        await db.execute(
            update(Wallet)
            .where(Wallet.id == transaction.wallet_id)
            .values(balance=Wallet.balance + transaction.amount)
        )
        await db.commit()
        await db.refresh(transaction)

        return {"payment_status": new_status}

    except Exception as e:
        logging.error(f"Error updating transaction status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update transaction status",
        )

    # Get charge configuration (using proper query)
    # charge_stmt = select(ChargeAndCommission)
    # charge_result = await db.execute(charge_stmt)
    # charge = charge_result.scalar_one_or_none()

    # if not charge:
    #     raise HTTPException(
    #         status_code=500, detail="Charge configuration not found"
    #     )

    # Get wallet
    # wallet = await get_wallet(transaction.wallet_id, db)

    # # Calculate the amount to add
    # # amount_to_add = calculate_net_amount(transaction.amount, charge)
    # amount_to_add = transaction.amount

    # # Update wallet balance
    # wallet.balance += amount_to_add

    # # Update transaction status
    # transaction.status = PaymentStatus.PAID

    # # Commit changes
    # await db.commit()
    # await db.refresh(transaction)
    # await db.refresh(wallet)

    # token = await get_user_notification_token(db=db, user_id=wallet.id)

    # if token:
    #     await send_push_notification(
    #         tokens=[token],
    #         title="Payment Received",
    #         message=f"Your wallet top-up of ₦{amount_to_add} has been received.",
    #     )

    # return {
    #     "payment_status": transaction.status,
    #     "wallet_balance": wallet.balance,
    #     "amount_added": str(amount_to_add),
    # }


# Helper function to calculate the net amount after charges
def calculate_net_amount(amount: Decimal, charge: ChargeAndCommission) -> Decimal:
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
        redis_client.delete("paid_pending_deliveries")

    elif tx_status == "cancelled":
        new_status = PaymentStatus.CANCELLED
        redis_client.delete("paid_pending_deliveries")
    else:
        new_status = PaymentStatus.FAILED
        redis_client.delete("paid_pending_deliveries")

    # Update payment status
    order_update_result = await db.execute(
        update(Order)
        .where(Order.id == UUID(tx_ref))
        .values(order_payment_status=new_status)
        .returning(Order.order_payment_status, Order.owner_id, Order.total_price)
    )

    redis_client.delete("paid_pending_deliveries")
    # Fetch the actual row data
    order_data = order_update_result.fetchone()
    if not order_data:
        raise HTTPException(status_code=404, detail="Order not found")

    owner_id = order_data.owner_id
    total_price = order_data.total_price

    # Get the current escrow balance
    wallet_result = await db.execute(
        select(Wallet.escrow_balance).where(Wallet.id == owner_id)
    )
    escrow_balance = wallet_result.scalar_one_or_none()

    if tx_status == "successful":
        # Uodate vendour escrow
        await db.execute(
            update(Wallet)
            .where(Wallet.id == owner_id)
            .values({"escrow_balance": escrow_balance + total_price})
        )
        await db.commit()

        token = await get_user_notification_token(db=db, user_id=owner_id)
        redis_client.delete(f"user_related_orders:{owner_id}")
        redis_client.delete("paid_pending_deliveries")

        if token:
            await send_push_notification(
                tokens=[token],
                title="Payment Received",
                message=f"Your payment of ₦{total_price} has been received.",
            )

    # delivery_stmt = select(Order.id).where(Order.id == UUID(tx_ref))
    # delivery_result = await db.execute(delivery_stmt)
    # delivery_id = delivery_result.scalar_one_or_none()

    # if delivery_id:
    #     redis_client.delete(f"delivery:{delivery_id}")
    # redis_client.delete("deliveries")

    return {"order_payment_status": order_data.order_payment_status}


async def pay_with_wallet(
    db: AsyncSession,
    order_id: UUID,
    buyer: User,
) -> dict:
    """
    Handles pay with wallet.

    Args:
        db: The database session.
        order_id: The ID of the order to pay.
        buyer: The authenticated user making the purchase.

    Returns:
        A status object with payment status and balances.

    Raises:
        HTTPException: Various exceptions for validation errors.
    """
    # Fetch order with related items, vendor, and delivery in one efficient query
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
            selectinload(Order.delivery),
        )
        .with_for_update()
    )
    result_order = await db.execute(stmt_order)
    order = result_order.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    # Fetch both wallets in parallel for better performance
    wallets_stmt = (
        select(Wallet)
        .where(Wallet.id.in_([buyer.id, order.vendor_id, order.delivery.dispatch_id]))
        .with_for_update()
    )
    result = await db.execute(wallets_stmt)
    wallets = result.scalars().all()

    # Map wallets by ID for easy access
    wallets_by_id = {wallet.id: wallet for wallet in wallets}
    buyer_wallet = wallets_by_id.get(buyer.id)
    seller_wallet = wallets_by_id.get(order.vendor_id)
    dispatch_wallet = wallets_by_id.get(order.delivery.dispatch_id)

    # Validate wallets
    if not buyer_wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Buyer wallet not found"
        )
    if not seller_wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Seller wallet not found"
        )

    # Determine amount to charge: total_price + delivery_fee if delivery required
    amount_to_charge = order.total_price
    if order.require_delivery == RequireDeliverySchema.DELIVERY:
        if order.delivery and order.delivery.delivery_fee:
            amount_to_charge += order.delivery.delivery_fee
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Delivery fee required but not found."
            )

    # Check if buyer has enough funds
    if buyer_wallet.balance < amount_to_charge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient funds in wallet",
        )


    # Process the payment
    buyer_wallet.balance -= amount_to_charge
    seller_wallet.escrow_balance += order.total_price  # Only vendor's share, not delivery fee

    # Only credit dispatch if available and delivery fee is present
    dispatch_transaction = None
    if (
        order.require_delivery == RequireDeliverySchema.DELIVERY
        and order.delivery
        and order.delivery.delivery_fee
        and order.delivery.dispatch_id
        and dispatch_wallet
    ):
        dispatch_wallet.escrow_balance += order.delivery.delivery_fee
        dispatch_transaction = {
            "wallet_id": dispatch_wallet.id,
            "amount": order.delivery.delivery_fee,
            "transaction_type": TransactionType.CREDIT,
            "payment_status": PaymentStatus.PAID,
            "created_at": current_time,
            "payment_method": PaymentMethod.WALLET,
            "payment_by": buyer.profile.full_name or buyer.profile.business_name,
            "updated_at": current_time,
        }

    # Create transactions in bulk
    current_time = datetime.now()
    transaction_values = [
        # Buyer transaction (DEBIT)
        {
            "wallet_id": buyer_wallet.id,
            "amount": amount_to_charge,
            "transaction_type": TransactionType.DEBIT,
            "payment_status": PaymentStatus.PAID,
            "created_at": current_time,
            "payment_method": PaymentMethod.WALLET,
            "payment_by": buyer.profile.full_name or buyer.profile.business_name,
            "updated_at": current_time,
        },
        # Seller transaction (CREDIT)
        {
            "wallet_id": seller_wallet.id,
            "amount": order.total_price,
            "transaction_type": TransactionType.CREDIT,
            "payment_status": PaymentStatus.PAID,
            "created_at": current_time,
            "payment_method": PaymentMethod.WALLET,
            "payment_by": buyer.profile.full_name or buyer.profile.business_name,
            "updated_at": current_time,
        },
    ]

    # Add dispatch transaction if applicable
    if dispatch_transaction:
        transaction_values.append(dispatch_transaction)

    await db.execute(insert(Transaction), transaction_values)

    # Update order status
    order.order_payment_status = PaymentStatus.PAID

    await db.commit()
    await db.refresh(order)

    # Send notifications
    buyer_token = await get_user_notification_token(db=db, user_id=buyer.id)
    seller_token = await get_user_notification_token(db=db, user_id=order.vendor_id)
    if buyer_token:
        await send_push_notification(
            tokens=[buyer_token],
            title="Payment Successful",
            message=f"Your payment of ₦{amount_to_charge} was successful.",
        )
    if seller_token:
        await send_push_notification(
            tokens=[seller_token],
            title="Order Paid",
            message=f"You have received a new order payment of ₦{order.total_price}.",
        )

    # Clear relevant caches
    redis_client.delete(f"user_related_orders:{buyer.id}")
    redis_client.delete(f"user_related_orders:{order.vendor_id}")
    redis_client.delete("paid_pending_deliveries")
    redis_client.delete("orders")

    return {
        "payment_status": order.order_payment_status,
        "charged_amount": amount_to_charge,
    }


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


async def make_withdrawal(db: AsyncSession, current_user: User) -> WithdrawalShema:
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
    charge = result.scalars().fetchone()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user.is_bloccked:
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
        withdrawal_amount = user.wallet.balance
        # Create withdrawal transaction (pending)
        withdrawal = Transaction(
            user_id=user.id,
            wallet_id=user.wallet.id,
            amount=withdrawal_amount,
            payment_status=PaymentStatus.PENDING,
            transaction_type=TransactionType.DEBIT,
        )
        db.add(withdrawal)
        await db.flush()

        # Deduct wallet balance immediately
        user.wallet.balance -= withdrawal_amount
        await db.flush()

        transfer_response = await transfer_money_to_user_account(
            bank_code=user.profile.bank_name,
            amount=str(withdrawal_amount),
            account_number=user.profile.bank_account_number,
            beneficiary_name=user.profile.account_holder_namev
            or user.profile.business_name
            or user.profile.full_name,
            charge=charge,
        )

        if transfer_response.get("status") == "success":
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
        else:
            # Refund wallet and set transaction to FAILED
            user.wallet.balance += withdrawal_amount
            withdrawal.payment_status = PaymentStatus.FAILED
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Bank transfer failed: {transfer_response.get('message', 'Unknown error occurred')}",
            )

    except Exception as e:
        await db.rollback()
        logger.error(f"Withdrawal failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process withdrawal",
        )


async def bank_payment_transfer_callback_old(request: Request, db: AsyncSession):
    payload = await request.json()
    event = payload.get("event")
    data = payload.get("data", {})

    # Optional: verify webhook signature for production
    secret_hash = settings.FLW_SECRET_HASH
    signature = request.headers.get("verif-hash")
    # if signature is None or signature != settings.FLW_SECRET_HASH:
    #     raise HTTPException(status_code=401, detail="Unauthorized")

    if not hmac.compare_digest(signature, secret_hash):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if event == "charge.completed" and data.get("status") == "successful":
        tx_ref = data.get("tx_ref")

        order = None
        if tx_ref:
            result = await db.execute(select(Order).where(Order.id == UUID(tx_ref)))
            order = result.scalar_one_or_none()

        if not order:
            return {"status": "ignored", "reason": "Order not found"}

        if order.order_payment_status == PaymentStatus.PAID:
            return {"status": "ignored", "reason": "Order already paid"}

        order.order_payment_status = PaymentStatus.PAID
        await db.commit()

        # Optionally, send notifications, update caches, etc.

        return {"status": "success", "order_id": order.id}

    return {"status": "ignored", "reason": "Not a successful charge.completed event"}
