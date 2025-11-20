import asyncio
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4
import logging
import uuid
from fastapi import BackgroundTasks, HTTPException, Request, status
from fastapi.responses import HTMLResponse
import httpx
from sqlalchemy import insert, select, update, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import hmac
from typing import Optional

from app.models.models import (
    ChargeAndCommission,
    Order,
    Item,
    TransactionLog,
    User,
    Wallet,
    Transaction,
    OrderItem,
   
)

from app.queue.producer import producer
from app.schemas.transaction_schema import (
    TransactionSchema,
    TransactionFilterSchema,
    TransactionResponseSchema,
)
from app.schemas.marketplace_schemas import (
    TopUpRequestSchema,
    TopUpResponseSchema,
    TransferDetailResponseSchema,
    WithdrawalShema,
)
from app.services.order_service import get_user_profile
from app.services.settings_service import get_charge_and_commission_settings
from app.schemas.order_schema import OrderType
from app.schemas.status_schema import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    RequireDeliverySchema,
    TransactionDirection,
    TransactionLogAction,
    TransactionType,
)
from app.utils.logger_config import setup_logger
from app.utils.utils import (
    get_fund_wallet_payment_link,
    transfer_money_to_user_account,
    verify_transaction_tx_ref,
    flutterwave_base_url,
    send_push_notification,
    get_user_notification_token,
)
from app.config.config import settings, redis_client
from app.templating import templates

logger = setup_logger()


async def clear_order_caches(order):
    """Clear all caches related to an order."""
    try:
        redis_client.delete(f"user_related_orders:{order.owner_id}")
        redis_client.delete(f"user_orders:{order.owner_id}")
        redis_client.delete(f"user_orders:{order.delivery.dispatch_id}")
        redis_client.delete(f"user_orders:{order.delivery.rider_id}")
        redis_client.delete(f"order_by_id:{order.id}")
        redis_client.delete(f'wallet_transactions:{order.owner_id}')
        redis_client.delete(f'wallet_transactions:{order.vendor_id}')
        redis_client.delete(f'wallet_transactions:{order.vendor_id}')
        redis_client.delete(f"user_orders:{order.vendor_id}")

        if order.delivery:
            redis_client.delete(f'wallet_transactions:{order.delivery.dispatch_id}')
            redis_client.delete(f'wallet_transactions:{order.delivery.sender_id}')
        redis_client.delete("paid_pending_deliveries")
        redis_client.delete("orders")
    except Exception as e:
        logger.warning(f"Failed to clear caches for order {order.id}: {str(e)}")


async def get_transactions(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    filters: Optional[TransactionFilterSchema] = None,
) -> TransactionResponseSchema:
    """
    Retrieve all transactions with filtering and pagination.

    Args:
        db: The database session.
        page: Page number (1-based).
        page_size: Number of items per page.
        filters: Optional filters to apply.

    Returns:
        TransactionResponseSchema with paginated transactions.

    Raises:
        HTTPException: If there's an error retrieving transactions.
    """
    try:
        # Build base query for all transactions
        stmt = select(Transaction)

        # Apply filters if provided
        if filters:
            conditions = []

            if filters.transaction_type:
                conditions.append(
                    Transaction.transaction_type == filters.transaction_type
                )

            if filters.payment_status:
                conditions.append(Transaction.payment_status == filters.payment_status)

            if filters.payment_method:
                conditions.append(Transaction.payment_method == filters.payment_method)

            if filters.start_date:
                conditions.append(Transaction.created_at >= filters.start_date)

            if filters.end_date:
                conditions.append(Transaction.created_at <= filters.end_date)

            if filters.min_amount:
                conditions.append(Transaction.amount >= filters.min_amount)

            if filters.max_amount:
                conditions.append(Transaction.amount <= filters.max_amount)

            if conditions:
                stmt = stmt.where(and_(*conditions))

        # Get total count for pagination
        count_stmt = select(func.count(Transaction.id))
        if filters:
            count_conditions = []
            if filters.transaction_type:
                count_conditions.append(
                    Transaction.transaction_type == filters.transaction_type
                )
            if filters.payment_status:
                count_conditions.append(
                    Transaction.payment_status == filters.payment_status
                )
            if filters.payment_method:
                count_conditions.append(
                    Transaction.payment_method == filters.payment_method
                )
            if filters.start_date:
                count_conditions.append(Transaction.created_at >= filters.start_date)
            if filters.end_date:
                count_conditions.append(Transaction.created_at <= filters.end_date)
            if filters.min_amount:
                count_conditions.append(Transaction.amount >= filters.min_amount)
            if filters.max_amount:
                count_conditions.append(Transaction.amount <= filters.max_amount)
            if count_conditions:
                count_stmt = count_stmt.where(and_(*count_conditions))

        count_result = await db.execute(count_stmt)
        total_count = count_result.scalar()

        # Apply pagination
        offset = (page - 1) * page_size
        stmt = (
            stmt.order_by(Transaction.created_at.desc()).offset(offset).limit(page_size)
        )

        # Execute query
        result = await db.execute(stmt)
        transactions = result.scalars().all()

        # Convert to schemas
        transaction_schemas = [
            TransactionSchema.model_validate(transaction)
            for transaction in transactions
        ]

        # Calculate pagination info
        total_pages = (total_count + page_size - 1) // page_size

        return TransactionResponseSchema(
            transactions=transaction_schemas,
            total_count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    except Exception as e:
        logger.error(f"Error retrieving transactions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve transactions",
        )


async def get_all_transactions(
    db: AsyncSession, skip: int = 0, limit: int = 20
) -> list[TransactionSchema]:
    """
    Retrieve all transactions from the database.

    Args:
        db: The database session.

    Returns:
        List of all transactions as Pydantic schemas.
    """
    try:
        stmt = (
            select(Transaction)
            .order_by(Transaction.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(stmt)
        transactions = result.scalars().all()
        return [
            TransactionSchema.model_validate(transaction)
            for transaction in transactions
        ]
    except Exception as e:
        logger.error(f"Error retrieving all transactions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve transactions",
        )


async def get_transaction(db: AsyncSession, transaction_id: UUID) -> TransactionSchema:
    """
    Retrieve a specific transaction by ID.

    Args:
        db: The database session.
        transaction_id: The ID of the transaction to retrieve.

    Returns:
        The transaction as a Pydantic schema.

    Raises:
        HTTPException: If transaction is not found.
    """
    try:
        stmt = select(Transaction).where(Transaction.id == transaction_id)
        result = await db.execute(stmt)
        transaction = result.scalar_one_or_none()

        if not transaction:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found"
            )

        return TransactionSchema.model_validate(transaction)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving transaction {transaction_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve transaction",
        )


async def handle_charge_completed_callback_fallback(
    request: Request, db: AsyncSession, payload=None
):
    """
    Fallback handler for charge.completed webhook events.
    Covers both order payments and wallet top-ups, applying correct escrow/wallet/transaction logic for all order types.
    Now includes robust error handling and UUID parsing.
    """
    try:
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
            order_uuid = None
            if tx_ref:
                try:
                    order_uuid = UUID(tx_ref)
                except Exception:
                    order_uuid = None
            if order_uuid:
                result = await db.execute(
                    select(Order).where(Order.id == order_uuid).with_for_update()
                )
                order = result.scalar_one_or_none()

            if order:
                # --- Handle Order Payment ---
                if order.order_payment_status == PaymentStatus.PAID:
                    # Only update payment method if needed (find the correct transaction by order id if exists)
                    await db.execute(
                        update(Transaction)
                        .where(
                            Transaction.wallet_id == order.owner_id,
                            Transaction.amount == order.total_price,
                        )
                        .values(payment_method=payment_type)
                    )
                    await db.commit()
                    return {"status": "ignored", "reason": "Order already paid"}

                # Mark order as paid
                order.order_payment_status = PaymentStatus.PAID
                await db.execute(
                    update(Transaction)
                    .where(
                        Transaction.wallet_id == order.owner_id,
                        Transaction.amount == order.total_price,
                    )
                    .values(payment_method=payment_type)
                )
                await db.commit()

                # --- Escrow/Wallet Movement Logic ---
                # Fetch customer wallet
                customer_wallet_result = await db.execute(
                    select(Wallet).where(Wallet.id == order.owner_id)
                )
                customer_wallet = customer_wallet_result.scalar_one_or_none()
                current_time = datetime.now()
                transaction_values = []

                if order.order_type == "package":
                    # Only delivery fee is moved to escrow
                    delivery_fee = None
                    if order.delivery_id:
                        delivery_result = await db.execute(
                            select(Order.delivery).where(Order.id == order.id)
                        )
                        delivery = delivery_result.scalar_one_or_none()
                        if delivery and hasattr(delivery, "delivery_fee"):
                            delivery_fee = delivery.delivery_fee
                    if not delivery_fee:
                        delivery_fee = order.total_price
                    if customer_wallet:
                        customer_wallet.escrow_balance += delivery_fee
                        transaction_values.append(
                            {
                                "wallet_id": customer_wallet.id,
                                "amount": delivery_fee,
                                "transaction_type": TransactionType.DEBIT,
                                "payment_status": PaymentStatus.PAID,
                                "created_at": current_time,
                                "payment_method": payment_type,
                                "updated_at": current_time,
                            }
                        )
                elif order.order_type in [OrderType.FOOD, OrderType.LAUNDRY]:
                    total_paid = order.total_price
                    delivery_fee = 0
                    if order.require_delivery == RequireDeliverySchema.DELIVERY:
                        if order.delivery_id:
                            delivery_result = await db.execute(
                                select(Order.delivery).where(Order.id == order.id)
                            )
                            delivery = delivery_result.scalar_one_or_none()
                            if delivery and hasattr(delivery, "delivery_fee"):
                                delivery_fee = delivery.delivery_fee
                        else:
                            delivery_fee = 0
                        total_paid += delivery_fee
                    if customer_wallet:
                        customer_wallet.escrow_balance += total_paid
                    # Move order amount to vendor escrow
                    vendor_wallet_result = await db.execute(
                        select(Wallet).where(Wallet.id == order.vendor_id)
                    )
                    vendor_wallet = vendor_wallet_result.scalar_one_or_none()
                    customer_name = None
                    if (
                        order.owner
                        and hasattr(order.owner, "profile")
                        and order.owner.profile
                    ):
                        customer_name = (
                            order.owner.profile.full_name
                            or order.owner.profile.business_name
                        )
                    vendor_name = None
                    if (
                        order.vendor
                        and hasattr(order.vendor, "profile")
                        and order.vendor.profile
                    ):
                        vendor_name = (
                            order.vendor.profile.business_name
                            or order.vendor.profile.full_name
                        )
                    if vendor_wallet:
                        vendor_wallet.escrow_balance += order.total_price
                    if customer_wallet:
                        transaction_values.append(
                            {
                                "wallet_id": customer_wallet.id,
                                "amount": total_paid,
                                "transaction_type": TransactionType.DEBIT,
                                "payment_status": PaymentStatus.PAID,
                                "created_at": current_time,
                                "payment_method": payment_type,
                                "to_user": vendor_name,
                                "updated_at": current_time,
                            }
                        )
                    if vendor_wallet:
                        transaction_values.append(
                            {
                                "wallet_id": vendor_wallet.id,
                                "amount": order.total_price,
                                "transaction_type": TransactionType.CREDIT,
                                "payment_status": PaymentStatus.PAID,
                                "created_at": current_time,
                                "payment_method": payment_type,
                                "from_user": customer_name,
                                "updated_at": current_time,
                            }
                        )
                if transaction_values:
                    await db.execute(insert(Transaction), transaction_values)
                    await db.commit()

                # Send notifications
                customer_token = await get_user_notification_token(
                    db=db, user_id=order.owner_id
                )
                vendor_token = await get_user_notification_token(
                    db=db, user_id=order.vendor_id
                )
                amount_str = (
                    f"₦{amount_paid}"
                    if currency == "NGN"
                    else f"{amount_paid} {currency}"
                )
                if customer_token:
                    await send_push_notification(
                        tokens=[customer_token],
                        title="Payment Successful",
                        message=f"Your payment of {amount_str} was successful.",
                    )
                if vendor_token and order.order_type in [
                    OrderType.FOOD,
                    OrderType.LAUNDRY,
                ]:
                    await send_push_notification(
                        tokens=[vendor_token],
                        title="Order Paid",
                        message=f"You have received a new order payment of {amount_str}.",
                    )

                # Clear caches
                redis_client.delete(f"user_related_orders:{order.owner_id}")
                redis_client.delete(f"user_orders:{order.owner_id}")
                redis_client.delete(f"user_orders:{order.vendor_id}")
                redis_client.delete("paid_pending_deliveries")
                redis_client.delete("orders")

                return {
                    "status": "success",
                    "order_id": str(order.id),
                    "payment_type": payment_type,
                }

            # --- If not an order, try as a wallet top-up transaction ---
            transaction = None
            transaction_uuid = None
            if tx_ref:
                try:
                    transaction_uuid = UUID(tx_ref)
                except Exception:
                    transaction_uuid = None
            if transaction_uuid:
                result = await db.execute(
                    select(Transaction).where(Transaction.id == transaction_uuid)
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
                    charge = await get_current_charge_settings(db)
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

        return {
            "status": "ignored",
            "reason": "Not a successful charge.completed event",
        }
    except Exception as e:
        await db.rollback()
        # Optionally log the error here
        raise HTTPException(
            status_code=500, detail=f"Payment callback failed: {str(e)}"
        )


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

        if wallet.balance >= 100_000 or (topup_data.amount + wallet.balance) > 100_000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Wallet balance cannot be more than NGN 100, 000",
            )

        # Create the transaction record
        transaction = Transaction(
            wallet_id=wallet.id,
            tx_ref=uuid.uuid4(),
            to_wallet_id=wallet.id,
            amount=topup_data.amount,
            transaction_type=TransactionType.FUND_WALLET,
            payment_status=PaymentStatus.PENDING,
            from_user=current_user.profile.full_name
            or current_user.profile.business_name,
            to_user="Self",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(transaction)

        # Fush to get the transaction ID for the payment link
        await db.flush()

        # Generate payment link
        payment_link = await get_fund_wallet_payment_link(
            id=transaction.tx_ref, amount=transaction.amount, current_user=current_user
        )

        # Update transaction with payment link
        transaction.payment_link = payment_link
        await db.commit()
        await db.refresh(transaction)

        return TopUpResponseSchema(
            amount=transaction.amount,
            payment_link=payment_link,
        )

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



# ==============================================================
# WALLET FUNDING CALLBACK
# ==============================================================
async def fund_wallet_callback(request: Request, db: AsyncSession):
    # Safely extract params (no KeyError crash)
    tx_ref = request.query_params.get("tx_ref")
    tx_status = request.query_params.get("status")
    transx_id = request.query_params.get("transaction_id")

    if not all([tx_ref, tx_status, transx_id]):
        raise HTTPException(status_code=400, detail="Missing tx_ref, status or transaction_id")

    logger.info(f"Wallet funding callback → tx_ref: {tx_ref} | status: {tx_status}")

    transaction = None

    try:
        # 1. Fetch transaction with lock
        result = await db.execute(
            select(Transaction)
            .where(Transaction.tx_ref == UUID(tx_ref))
            .with_for_update()
        )
        transaction = result.scalar_one_or_none()

        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")

        # 2. IDEMPOTENCY: Already funded? → Show success and exit
        if transaction.payment_status == PaymentStatus.PAID:
            logger.info(f"Idempotent: Wallet funding already completed for tx_ref {tx_ref}")
            return _render_success_page(transaction, request, transx_id)

        # 3. Verify with provider — THIS IS THE ONLY TRUTH
        verify_tranx = await verify_transaction_tx_ref(tx_ref)
        if not verify_tranx or not isinstance(verify_tranx, dict):
            logger.error(f"Invalid verification response for {tx_ref}")
            raise HTTPException(status_code=502, detail="Payment gateway error")

        verify_data = verify_tranx.get("data", {})
        verify_status = verify_data.get("status")

        verified_success = (verify_status == "successful")

        # 4. Final status decision
        if verified_success and tx_status == "successful":
            new_status = PaymentStatus.PAID
        elif tx_status == "cancelled":
            new_status = PaymentStatus.CANCELLED
        else:
            new_status = PaymentStatus.FAILED

        # 5. COMMIT TO DB FIRST — THIS IS SACRED
        transaction.payment_status = new_status
        transaction.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(transaction)

        logger.info(f"Transaction {tx_ref} → {new_status.value}")

        # 6. ONLY AFTER commit → fire side effects (they can fail, money is safe)
        if new_status == PaymentStatus.PAID:
            try:
                await _credit_wallet_safely(transaction, transx_id)
            except Exception as e:
                logger.critical(
                    f"CRITICAL: Wallet funding PAID but side effects FAILED! tx_ref: {tx_ref} | Error: {e}",
                    exc_info=True
                )
                # Send alert — this is emergency
                await _alert_wallet_funding_failure(transaction, e, db=db)
                

        # 7. Always show success/failure page — even if template crash
        return _render_success_page(transaction, request, transx_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Fatal error in fund_wallet_callback: {tx_ref}")
        # Last resort — user must not see 500
        return HTMLResponse(
            "<h1>Wallet Funding In Progress</h1>"
            "<p>Your payment was received. We're crediting your wallet now.<br>"
            "Refresh in 1 minute or contact support if not credited.</p>",
            status_code=200,
        )


# ==============================================================
# CREDIT WALLET — FIRE AND FORGET (but logged + alerted if fail)
# ==============================================================
async def _credit_wallet_safely(transaction: Transaction, transx_id: str):
    amount_str = f"{transaction.amount:.2f}"

    # Update transaction status
    await producer.publish_message(
        service="wallet",
        operation="update_transaction",
        payload={
            "wallet_id": str(transaction.wallet_id),
            "tx_ref": str(transaction.tx_ref),
            "payment_status": PaymentStatus.PAID,
            "payment_method": PaymentMethod.CARD,
            "transaction_direction": TransactionDirection.CREDIT,
            "is_fund_wallet": True,
            "transaction_id": transx_id,
        },
    )

    # Credit wallet balance
    await producer.publish_message(
        service="wallet",
        operation="update_wallet",
        payload={
            "wallet_id": str(transaction.wallet_id),
            "balance_change": amount_str,
            "escrow_change": "0",
        },
    )

    logger.info(f"Wallet credited: +₦{amount_str} → wallet {transaction.wallet_id}")


# ==============================================================
# RENDER PAGE — SAFE EVEN IF JINJA CRASH
# ==============================================================
def _render_success_page(transaction: Transaction, request: Request, transx_id: str):
    try:
        context = {
            "request": request,
            "payment_status": transaction.payment_status.value,
            "amount": f"{transaction.amount:,.2f}",
            "date": transaction.updated_at.strftime("%b %d, %Y"),
            "transaction_id": transx_id,
        }
        return templates.TemplateResponse("payment-status.html", context)
    except Exception as e:
        logger.error(f"Template failed in fund_wallet_callback: {e}", exc_info=True)
        return HTMLResponse(
            f"<h1>Wallet Funding {transaction.payment_status.value.title()}!</h1>"
            f"<p>Amount: ₦{transaction.amount:,.2f}<br>"
            f"Date: {datetime.utcnow().strftime('%b %d, %Y')}</p>"
            f"<p>If not credited in 2 mins, contact support with ID: {transx_id}</p>",
            status_code=200,
        )


# ==============================================================
# EMERGENCY ALERT — WHEN MONEY IS PAID BUT NOT CREDITED
# ==============================================================
async def _alert_wallet_funding_failure(transaction: Transaction, db: AsyncSession, error: Exception):
    """
    CRITICAL: User paid, DB says PAID, but wallet not credited!
    We log this as a FAILED_INTERNAL action so ops can reconcile.
    """
    log_entry = TransactionLog(
        id=uuid4(),
        vendor_id=transaction.wallet.id if hasattr(transaction.wallet, 'id') else None,
        order_id=None,
        amount=transaction.amount,
        action=TransactionLogAction.WALLET_FUNDING_FAILED_INTERNAL,
        status=PaymentStatus.FAILED,
        details={
            "error": str(error),
            "error_type": error.__class__.__name__,
            "tx_ref": str(transaction.tx_ref),
            "wallet_id": str(transaction.wallet_id),
            "transaction_id": transaction.id,
            "alert_type": "WALLET_FUNDING_SIDE_EFFECT_FAILURE",
            "requires_manual_reconciliation": True,
            "timestamp": datetime.utcnow().isoformat(),
        },
        timestamp=datetime.utcnow(),
    )

    try:
        db.add(log_entry)
        await db.commit()
        logger.critical(
            f"WALLET FUNDING EMERGENCY LOGGED → tx_ref: {transaction.tx_ref} | "
            f"Amount: ₦{transaction.amount:,.2f} | Log ID: {log_entry.id}"
        )
    except Exception as log_error:
        logger.critical(
            f"FAILED TO EVEN LOG THE WALLET FUNDING FAILURE! tx_ref: {transaction.tx_ref} | "
            f"Log error: {log_error}", exc_info=True)



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


async def get_current_charge_settings(db: AsyncSession) -> ChargeAndCommission:
    """
    Get current charge settings from database using the settings service.

    Args:
        db: Database session

    Returns:
        ChargeAndCommission object with current settings

    Raises:
        HTTPException: If no settings are configured
    """
    settings_schema = await get_charge_and_commission_settings(db)
    if not settings_schema:
        # Return default settings if none configured
        logger.warning("No charge settings found in database, using default values")
        default_settings = ChargeAndCommission(
            id=0,
            payout_charge_transaction_upto_5000_naira=Decimal("25.00"),
            payout_charge_transaction_from_5001_to_50_000_naira=Decimal("50.00"),
            payout_charge_transaction_above_50_000_naira=Decimal("100.00"),
            value_added_tax=Decimal("0.075"),
        )
        return default_settings

    # Convert schema back to model for compatibility
    charge_model = ChargeAndCommission(
        id=settings_schema.id,
        payout_charge_transaction_upto_5000_naira=settings_schema.payout_charge_transaction_upto_5000_naira,
        payout_charge_transaction_from_5001_to_50_000_naira=settings_schema.payout_charge_transaction_from_5001_to_50_000_naira,
        payout_charge_transaction_above_50_000_naira=settings_schema.payout_charge_transaction_above_50_000_naira,
        value_added_tax=settings_schema.value_added_tax,
    )
    return charge_model



# ===================================================================
# ROBUST FLUTTERWAVE WEBHOOK + CALLBACK IMPLEMENTATION
# ===================================================================

async def handle_payment_webhook(request: Request, db: AsyncSession):
    """
    Flutterwave webhook handler with idempotency and error recovery.
    This is the PRIMARY payment processor - callback is backup.
    """
    try:
        payload = await request.json()
        
        # 1. Validate webhook signature
        signature = request.headers.get("verif-hash")
        if not signature or signature != settings.FLW_SECRET_HASH:
            logger.warning(f"Invalid webhook signature: {signature}")
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        # 2. Extract payment data
        event = payload.get("event")
        data = payload.get("data", {})
        tx_ref = data.get("tx_ref")
        status = data.get("status")
        flw_ref = data.get("flw_ref")
        amount = data.get("amount")
        
        if not tx_ref or not flw_ref:
            logger.error(f"Missing tx_ref or flw_ref in webhook: {payload}")
            raise HTTPException(status_code=400, detail="Invalid webhook payload")
        
        logger.info(
            f"Webhook received: event={event}, tx_ref={tx_ref}, "
            f"status={status}, flw_ref={flw_ref}"
        )
        
        # 3. Webhook idempotency (prevents duplicate webhook processing)
        webhook_key = f"webhook_received:{tx_ref}:{flw_ref}"
        if redis_client.get(webhook_key):
            logger.info(f"Webhook already processed: {webhook_key}")
            return {"message": "already_processed", "tx_ref": tx_ref}
        
        # 4. Validate payment status
        if status != "successful":
            logger.warning(f"Non-successful payment status: {status} for {tx_ref}")
            redis_client.setex(webhook_key, 3600, "failed")
            return {"message": "payment_not_successful", "status": status}
        
        # 5. Convert tx_ref to UUID
        try:
            tx_ref_uuid = UUID(tx_ref)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid tx_ref format: {tx_ref} - {e}")
            raise HTTPException(status_code=400, detail="Invalid tx_ref format")
        
        # 6. Fetch order with lock
        result = await db.execute(
            select(Order)
            .where(Order.tx_ref == tx_ref_uuid)
            .options(
                selectinload(Order.owner).selectinload(User.profile),
                selectinload(Order.vendor).selectinload(User.profile),
                selectinload(Order.delivery),
            )
            .with_for_update()
        )
        order = result.scalar_one_or_none()
        
        if not order:
            logger.warning(f"Order not found for tx_ref: {tx_ref}")
            redis_client.setex(webhook_key, 3600, "not_found")
            return {"message": "order_not_found"}
        
        # 7. Check if already paid (by previous webhook or callback)
        if order.order_payment_status == PaymentStatus.PAID:
            logger.info(f"Order {order.id} already PAID")
            redis_client.setex(webhook_key, 86400, "already_paid")
            
            # Still try side effects in case they failed before
            asyncio.create_task(_run_payment_side_effects_once(order, db, tx_ref))
            
            return {"message": "already_paid", "order_id": str(order.id)}
        
        # 8. Verify with Flutterwave (source of truth)
        try:
            verify_result = await verify_transaction_tx_ref(tx_ref)
            verified = (
                verify_result 
                and verify_result.get("status") == "success"
                and verify_result.get("data", {}).get("status") == "successful"
            )
            
            if not verified:
                logger.error(f"Verification failed for {tx_ref}: {verify_result}")
                redis_client.setex(webhook_key, 3600, "verification_failed")
                return {"message": "verification_failed"}
                
        except Exception as e:
            logger.error(f"Verification error for {tx_ref}: {e}", exc_info=True)
            # Don't mark as processed - allow retry
            return {"message": "verification_error"}
        
        # 9. Validate amount
        expected_amount = _get_expected_amount(order)
        if expected_amount and abs(Decimal(amount) - expected_amount) > Decimal("0.01"):
            logger.error(
                f"Amount mismatch for order {order.id}: "
                f"expected={expected_amount}, received={amount}"
            )
            redis_client.setex(webhook_key, 86400, "amount_mismatch")
            return {"message": "amount_mismatch"}
        
        # 10. UPDATE DATABASE FIRST (CRITICAL - THIS IS THE COMMITMENT)
        try:
            order.order_payment_status = PaymentStatus.PAID
            order.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(order)
            
            logger.info(f"✓ Order {order.id} marked PAID via webhook")
            
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to update order {order.id}: {e}", exc_info=True)
            # Don't mark webhook as processed - allow retry
            raise HTTPException(status_code=500, detail="Database update failed")
        
        # 11. Mark webhook as processed AFTER successful DB commit
        redis_client.setex(webhook_key, 86400, "processed")
        
        # 12. Run side effects asynchronously (idempotent)
        asyncio.create_task(_run_payment_side_effects_once(order, db, tx_ref))
        asyncio.create_task(_create_payment_audit_log_once(order))
        
        logger.info(f"✓ Webhook processed successfully for order {order.id}")
        
        return {
            "message": "success",
            "order_id": str(order.id),
            "status": "paid"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


async def order_payment_callback(request: Request, db: AsyncSession):
    """
    Payment callback (backup processor if webhook fails).
    This is the USER-FACING response after payment.
    """
    tx_ref = request.query_params.get("tx_ref")
    tx_status = request.query_params.get("status")
    transx_id = request.query_params.get("transaction_id")
    
    if not tx_ref:
        raise HTTPException(status_code=400, detail="Missing tx_ref")
    
    logger.info(f"Callback received: tx_ref={tx_ref}, status={tx_status}")
    
    try:
        # 1. Convert tx_ref to UUID
        try:
            tx_ref_uuid = UUID(tx_ref)
        except (ValueError, TypeError):
            logger.error(f"Invalid tx_ref format: {tx_ref}")
            raise HTTPException(status_code=400, detail="Invalid tx_ref")
        
        # 2. Fetch order (no lock needed - read-only check first)
        result = await db.execute(
            select(Order)
            .where(Order.tx_ref == tx_ref_uuid)
            .options(
                selectinload(Order.delivery),
                selectinload(Order.owner).selectinload(User.profile),
                selectinload(Order.vendor).selectinload(User.profile),
            )
        )
        order = result.scalar_one_or_none()
        
        if not order:
            logger.error(f"Order not found for tx_ref: {tx_ref}")
            raise HTTPException(status_code=404, detail="Order not found")
        
        # 3. Check if already paid (webhook likely processed it)
        if order.order_payment_status == PaymentStatus.PAID:
            logger.info(f"Order {order.id} already PAID (likely via webhook)")
            
            # Try side effects in case webhook failed on that part
            asyncio.create_task(_run_payment_side_effects_once(order, db, tx_ref))
            asyncio.create_task(_create_payment_audit_log_once(order))
            
            return await _render_payment_page(
                order, request, transx_id, is_payment_success=True
            )
        
        # 4. Webhook didn't process it - callback becomes primary processor
        logger.warning(f"Webhook missed order {order.id} - callback processing")
        
        # 5. Verify with Flutterwave
        try:
            verify_result = await verify_transaction_tx_ref(tx_ref)
            verified_success = (
                verify_result
                and verify_result.get("status") == "success"
                and verify_result.get("data", {}).get("status") == "successful"
            )
        except Exception as e:
            logger.error(f"Verification error: {e}", exc_info=True)
            verified_success = False
        
        # 6. Determine status
        if verified_success and tx_status == "successful":
            new_status = PaymentStatus.PAID
        elif tx_status == "cancelled":
            new_status = PaymentStatus.CANCELLED
        else:
            new_status = PaymentStatus.FAILED
        
        # 7. Acquire lock and update
        result_locked = await db.execute(
            select(Order)
            .where(Order.id == order.id)
            .with_for_update()
        )
        order_locked = result_locked.scalar_one()
        
        # Double-check payment status after lock
        if order_locked.order_payment_status == PaymentStatus.PAID:
            logger.info(f"Order {order.id} paid by another process during lock wait")
            asyncio.create_task(_run_payment_side_effects_once(order_locked, db, tx_ref))
            return await _render_payment_page(
                order_locked, request, transx_id, is_payment_success=True
            )
        
        # 8. Update order status
        order_locked.order_payment_status = new_status
        order_locked.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(order_locked)
        
        logger.info(f"Order {order_locked.id} marked as {new_status.value} via callback")
        
        # 9. Run side effects for successful payments
        if new_status == PaymentStatus.PAID:
            asyncio.create_task(_run_payment_side_effects_once(order_locked, db, tx_ref))
            asyncio.create_task(_create_payment_audit_log_once(order_locked))
        
        # 10. Handle failed/cancelled
        elif new_status in (PaymentStatus.CANCELLED, PaymentStatus.FAILED):
            if (
                order_locked.order_type == OrderType.PACKAGE 
                and order_locked.delivery 
                and order_locked.delivery.rider_id
            ):
                await db.execute(
                    update(User)
                    .where(User.id == order_locked.delivery.rider_id)
                    .values(has_delivery=False)
                )
                order_locked.delivery.rider_id = None
                order_locked.delivery.dispatch_id = None
                order_locked.delivery.rider_phone_number = None
                await db.commit()
        
        # 11. Clear caches
        await clear_order_caches(order_locked)
        
        # 12. Render page
        return await _render_payment_page(
            order_locked,
            request,
            transx_id,
            is_payment_success=(new_status == PaymentStatus.PAID)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Critical callback error: {tx_ref}")
        # Return user-friendly page even on error
        return HTMLResponse(
            "<h1>Payment Received</h1>"
            "<p>We're processing your payment. Please check your orders in a few minutes.</p>"
            "<p>If payment doesn't reflect in 5 minutes, contact support.</p>",
            status_code=200,
        )


# ===================================================================
# IDEMPOTENT SIDE EFFECTS
# ===================================================================

async def _run_payment_side_effects_once(order: Order, db: AsyncSession, tx_ref: str):
    """
    Run wallet updates and transactions with strict idempotency.
    Can be called by both webhook and callback safely.
    """
    side_effects_key = f"payment_side_effects:{order.id}:{tx_ref}"
    
    # Check if already done
    if redis_client.get(side_effects_key):
        logger.info(f"Side effects already processed for order {order.id}")
        return
    
    # Try to acquire lock (5 min window)
    locked = redis_client.set(side_effects_key, "running", nx=True, ex=300)
    if not locked:
        logger.info(f"Another process running side effects for {order.id}")
        return
    
    try:
        logger.info(f"Processing side effects for order {order.id}")
        
        # Run the actual payment processing
        await _process_successful_payment(order, db, tx_ref)
        
        # Mark as completed (90 days TTL)
        redis_client.setex(side_effects_key, 86400 * 90, "completed")
        
        logger.info(f"✓ Side effects completed for order {order.id}")
        
    except Exception as e:
        logger.error(f"Side effects failed for order {order.id}: {e}", exc_info=True)
        
        # Alert admin
        await _alert_admin_partial_failure(
            order=order,
            db=db,
            e=e
        )
        
        # Remove lock to allow retry
        redis_client.delete(side_effects_key)


async def _create_payment_audit_log_once(order: Order):
    """Create audit log with idempotency."""
    audit_key = f"audit_log:payment:{order.id}"
    
    if redis_client.get(audit_key):
        return
    
    if not redis_client.set(audit_key, "running", nx=True, ex=600):
        return
    
    try:
        await producer.publish_message(
            service="audit",
            operation="create_transaction_log",
            payload={
                "order_id": str(order.id),
                "vendor_id": str(order.vendor_id) if order.vendor_id else None,
                "amount": str(order.grand_total),
                "action": TransactionLogAction.RECEIVED,
                "status": order.order_payment_status,
                "details": {
                    "order_type": order.order_type.value,
                    "order_number": order.order_number,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            },
        )
        
        redis_client.setex(audit_key, 86400 * 90, "done")
        logger.info(f"Audit log created for order {order.id}")
        
    except Exception as e:
        logger.error(f"Audit log failed for order {order.id}: {e}")
        redis_client.delete(audit_key)


# ===================================================================
# HELPER FUNCTIONS
# ===================================================================

def _get_expected_amount(order: Order) -> Decimal:
    """Get expected payment amount based on order type."""
    if order.order_type == OrderType.PACKAGE:
        return Decimal(order.delivery.delivery_fee) if order.delivery else Decimal(0)
    else:
        return Decimal(order.grand_total)


async def _render_payment_page(
    order: Order,
    request: Request,
    transx_id: str,
    is_payment_success: bool = False
) -> HTMLResponse:
    """Render payment status page safely."""
    try:
        amount = _get_expected_amount(order)
        
        context = {
            "request": request,
            "payment_status": order.order_payment_status.value,
            "amount": f"{amount:,.2f}",
            "date": order.updated_at.strftime("%b %d, %Y"),
            "transaction_id": transx_id or "N/A",
            "order_number": order.order_number,
        }
        
        return templates.TemplateResponse("payment-status.html", context)
        
    except Exception as e:
        logger.error(f"Template rendering failed: {e}", exc_info=True)
        
        # Fallback: Simple HTML
        status_text = order.order_payment_status.value.title()
        note = (
            "Your payment was successful but we encountered a display issue. "
            "Your order has been processed."
            if is_payment_success
            else "Please contact support if you need assistance."
        )
        
        return HTMLResponse(
            f"""
            <html>
            <head><title>Payment {status_text}</title></head>
            <body>
                <h1>Payment {status_text}</h1>
                <p><strong>Amount:</strong> ₦{amount:,.2f}</p>
                <p><strong>Order:</strong> {order.order_number}</p>
                <p><strong>Transaction ID:</strong> {transx_id or 'N/A'}</p>
                <p><strong>Date:</strong> {order.updated_at.strftime('%b %d, %Y')}</p>
                <p>{note}</p>
            </body>
            </html>
            """,
            status_code=200
        )


# ===================================================================
# FOOD / LAUNDRY / DELIVERY
# ===================================================================
async def _process_successful_payment(order: Order, db: AsyncSession, tx_ref: str):
    customer = order.owner
    vendor = order.vendor
    charged_amount = Decimal(order.grand_total)

    # PACKAGE ORDER — only delivery fee to customer escrow
    if order.order_type == OrderType.PACKAGE:
        if not order.delivery or not order.delivery.delivery_fee:
            logger.error(f"Package order {order.id} missing delivery fee")
            return
        amount = Decimal(order.delivery.delivery_fee)

        # Customer escrow + delivery fee
        await producer.publish_message(
            service="wallet",
            operation="update_wallet",
            payload={
                "wallet_id": str(order.owner_id),
                "escrow_change": str(amount),
                "balance_change": "0",
            },
        )

        # Transaction record (customer → platform for delivery)
        await producer.publish_message(
            service="wallet",
            operation="create_transaction",
            payload={
                "wallet_id": str(order.owner_id),
                "tx_ref": tx_ref,
                "amount": str(amount),
                "transaction_type": TransactionType.USER_TO_USER,
                "transaction_direction": TransactionDirection.DEBIT,
                "payment_status": order.order_payment_status,
                "payment_method": PaymentMethod.CARD,
                "from_user": customer.profile.full_name or customer.profile.business_name,
                "to_user": "Platform (Delivery Fee)",
            },
        )

        await _send_notifications(db=db, order=order)


    # FOOD & LAUNDRY — full amount to both escrows
    elif order.order_type in (OrderType.FOOD, OrderType.LAUNDRY):
        if not order.vendor_id:
            return

        # Customer escrow (money held)
        await producer.publish_message(
            service="wallet",
            operation="update_wallet",
            payload={
                "wallet_id": str(order.owner_id),
                "escrow_change": str(charged_amount),
                "balance_change": "0",
            },
        )

        # Vendor escrow (money held until delivery)
        await producer.publish_message(
            service="wallet",
            operation="update_wallet",
            payload={
                "wallet_id": str(order.vendor_id),
                "escrow_change": str(charged_amount),
                "balance_change": "0",
            },
        )

        # Transaction: Customer → Vendor (escrowed)
        await producer.publish_message(
            service="wallet",
            operation="create_transaction",
            payload={
                "wallet_id": str(order.owner_id),
                "tx_ref": tx_ref,
                "to_wallet_id": str(order.vendor_id),
                "amount": str(charged_amount),
                "transaction_type": TransactionType.USER_TO_USER,
                "transaction_direction": TransactionDirection.DEBIT,
                "payment_status": order.order_payment_status,
                "payment_method": PaymentMethod.CARD,
                "from_user": customer.profile.full_name or customer.profile.business_name,
                "to_user": vendor.profile.business_name or vendor.profile.full_name
            },
        )

        # Vendor receives credit (escrowed)
        await producer.publish_message(
            service="wallet",
            operation="create_transaction",
            payload={
                "wallet_id": str(order.vendor_id),
                "tx_ref": tx_ref,
                "from_wallet_id": str(order.owner_id),
                "amount": str(charged_amount),
                "transaction_type": TransactionType.USER_TO_USER,
                "transaction_direction": TransactionDirection.CREDIT,
                "payment_status": PaymentStatus.ESCROWED,
                "payment_method": PaymentMethod.CARD,
                "from_user": customer.profile.full_name or customer.profile.business_name,
                "to_user": vendor.profile.business_name or vendor.profile.full_name,
            },
        )

        await _send_notifications(db=db, order=order)
        

    # Common: Notify + update order status
    await producer.publish_message(
        service="order_status",
        operation="order_payment_status",
        payload={"new_status": PaymentStatus.PAID, "order_id": str(order.id)}
    )

    await clear_order_caches(order)


async def _alert_admin_partial_failure(order: Order, db: AsyncSession, error: Exception):
    """
    CRITICAL: User paid, DB says PAID, but wallet not credited!
    We log this as a FAILED_INTERNAL action so ops can reconcile.
    """
    log_entry = TransactionLog(
        id=uuid4(),
        vendor_id=order.vendor_id,
        order_id=order.id,
        amount=order.grand_total,
        action=TransactionLogAction.FAILED,
        status=order.order_payment_status,
        details={
            "error": str(error),
            "tx_ref": str(order.tx_ref),
            "wallet_id": str(order.owner_id),
            "order_id": order.id,
            "alert_type": "ORDER_PAYMENT_SIDE_EFFECT_FAILURE",
            "requires_manual_reconciliation": True,
            "timestamp": datetime.now().isoformat(),
        },
        timestamp=datetime.now(),
    )

    try:
        db.add(log_entry)
        await db.commit()
        logger.critical(
            f"WALLET FUNDING EMERGENCY LOGGED → tx_ref: {order.tx_ref} | "
            f"Amount: ₦{order.grand_total:,.2f} | Log ID: {log_entry.id}"
        )
    except Exception as log_error:
        logger.critical(
            f"FAILED TO EVEN LOG THE WALLET FUNDING FAILURE! tx_ref: {order.tx_ref} | "
            f"Log error: {log_error}", exc_info=True)


async def _send_notifications(db: AsyncSession, order: Order):
    # Extract the unique user IDs we need tokens for
    user_ids = {
        order.owner_id,
        order.vendor_id,
    }
    
    # Add rider_id only if delivery exists
    if order.delivery and order.delivery.rider_id:
        user_ids.add(order.delivery.rider_id)
    
    # Remove None values just in case
    user_ids = {uid for uid in user_ids if uid is not None}

    if not user_ids:
        return

    # get all tokens in one go
    result = await db.execute(
        select(User.id, User.notification_token)
        .where(User.id.in_(user_ids), User.notification_token.is_not(None))
    )
    token_map = {row.id: row.notification_token for row in result.all()}

    customer_token = token_map.get(order.owner_id)
    rider_token    = token_map.get(order.delivery.rider_id) if order.delivery else None
    vendor_token   = token_map.get(order.vendor_id)

    # Send notifications
    async with asyncio.TaskGroup() as tg:
        if customer_token:
            tg.create_task(
                send_push_notification(
                    tokens=[customer_token],
                    title="Payment Successful",
                    message=f"Your payment of ₦{order.grand_total:,.2f} is successful.",
                )
            )
        if rider_token:
            tg.create_task(
                send_push_notification(
                    tokens=[rider_token],
                    title="New order",
                    message="You have a new order.",
                    navigate_to="/delivery/orders",
                )
            )
        if vendor_token:
            tg.create_task(
                send_push_notification(
                    tokens=[vendor_token],
                    title="New order",
                    message="You have a new order.",
                    navigate_to="/delivery/orders",
                )
            )

async def product_order_payment_callback(request: Request, db: AsyncSession):
    """
    CRITICAL PAYMENT CALLBACK — MUST never lose a successful payment
    """
    tx_ref = request.query_params.get("tx_ref")
    tx_status = request.query_params.get("status")
    transx_id = request.query_params.get("transaction_id")

    # 1. Early validation
    if not all([tx_ref, tx_status, transx_id]):
        raise HTTPException(status_code=400, detail="Missing required parameters")

    order = None
    payment_was_processed = False

    try:
        # 2. Verify with payment provider (Flutterwave/Paystack/etc) — THIS IS THE SOURCE OF TRUTH
        verify_tranx = await verify_transaction_tx_ref(tx_ref)
        if not verify_tranx or verify_tranx.get("status") != "success":
            logger.warning(f"Payment verification failed for tx_ref: {tx_ref}")
            new_status = PaymentStatus.FAILED
        elif tx_status == "successful":
            new_status = PaymentStatus.PAID
        else:
            new_status = PaymentStatus.FAILED

        # 3. Fetch order with lock
        result = await db.execute(
            select(Order)
            .where(Order.tx_ref == UUID(tx_ref))
            .where(Order.order_type == OrderType.PRODUCT)
            .options(
                selectinload(Order.order_items).selectinload(OrderItem.item),
                selectinload(Order.owner),
                selectinload(Order.vendor)
            )
            .with_for_update()
        )
        order = result.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        # 4. IDEMPOTENCY: If already paid → just show success page (safe early return)
        if order.order_payment_status == PaymentStatus.PAID:
            logger.info(f"Order {order.id} already paid. Idempotent callback.")
            return _render_payment_status(order, request, transx_id, PaymentStatus.PAID)

        # 5. ONLY if verified successful → mark as paid FIRST
        if new_status == PaymentStatus.PAID:
            order.order_payment_status = PaymentStatus.PAID
            order.updated_at = datetime.now()
            await db.commit()      
            await db.refresh(order)
            logger.info(f"Payment marked PAID for order {order.id} (tx_ref: {tx_ref})")
            payment_was_processed = True

            # 6. All the risky/async side effects (fire-and-forget if needed)
            try:
                if payment_was_processed:
                    await _process_successful_payment_side_effects(order, db)
            except Exception as side_effect_error:
                logger.critical(
                    f"CRITICAL: Payment marked PAID but side effects failed for order {order.id}: {side_effect_error}",
                    exc_info=True
                )
                # DO NOT REVERT THE PAYMENT — money was taken!
                # Instead: trigger alert, retry queue, or manual reconciliation
                await trigger_payment_reconciliation_alert(order, side_effect_error)

        else:
            # Failed or cancelled
            order.order_payment_status = new_status
            order.updated_at = datetime.utcnow()
            await db.commit()
            logger.info(f"Order {order.id} marked as {new_status}")

        # 7. ALWAYS return HTML — even if things partially failed
        # This can NEVER throw ResponseValidationError if route is not under
        return _render_payment_status(order, request, transx_id, order.order_payment_status)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in payment callback {tx_ref}: {e}", exc_info=True)
        # Even on total failure — if we already marked as PAID, don't lie
        final_status = order.order_payment_status if order else PaymentStatus.FAILED
        try:
            return _render_payment_status(order or {}, request, transx_id, final_status, error=True)
        except:
            # Absolute last resort — raw HTML to avoid template crash
            context = {
            "request": request,
            "payment_status": final_status,
            "amount": f"{order.grand_total:,.2f}",
            "date": order.updated_at.strftime("%b %d, %Y"),
            "transaction_id": transx_id or "N/A",
            "order_number": order.order_number,
            "note": 'We received your payment but encountered an issue. Support has been notified.' if payment_was_processed  else 'N/A'
        }

            return templates.TemplateResponse("payment-processing.html", context)
            # return HTMLResponse(
            #     "<h1>Payment Processing Error</h1><p>We received your payment but encountered an issue. Support has been notified.</p>",
            #     status_code=500
            # )

# ———————— Helper: Render template safely ————————
def _render_payment_status(order, request: Request, transx_id: str, payment_status, error=False):
    try:
        amount = f"{getattr(order, 'grand_total', 0):.2f}"
        order_number = getattr(order, 'order_number', 'N/A')
        date_str = datetime.now().strftime("%b %d, %Y")
        
        context = {
            "request": request,
            "payment_status": payment_status,
            "amount": amount,
            "date": date_str,
            "transaction_id": transx_id,
            "order_number": order_number,
            "error": error,
        }
        return templates.TemplateResponse("payment-status.html", context)
    except Exception as render_error:
        logger.error(f"Failed to render payment-status.html: {render_error}", exc_info=True)
        raise

# ———————— Helper: All side effects after payment is secured ————————
async def _process_successful_payment_side_effects(order: Order, db: AsyncSession):
    customer = order.owner.profile
    vendor = order.vendor.profile

    total = Decimal(order.grand_total)

    # ESCROW BOTH SIDES
    await producer.publish_message(
        service="wallet",
        operation="update_wallet",
        payload={
            "wallet_id": str(order.owner_id),
            "escrow_change": str(total),
            "balance_change": "0",
        },
    )
    await producer.publish_message(
        service="wallet",
        operation="update_wallet",
        payload={
            "wallet_id": str(order.vendor_id),
            "escrow_change": str(total),
            "balance_change": "0",
        },
    )

    # TRANSACTION: Customer debit
    await producer.publish_message(
        service="wallet",
        operation="create_transaction",
        payload={
            "wallet_id": str(order.owner_id),
            "tx_ref": str(order.tx_ref),
            "to_wallet_id": str(order.vendor_id),
            "amount": str(total),
            "transaction_type": TransactionType.USER_TO_USER,
            "transaction_direction": TransactionDirection.DEBIT,
            "payment_status": order.order_payment_status,
            "payment_method": PaymentMethod.CARD,
            "from_user": customer.full_name or customer.business_name,
            "to_user": vendor.business_name or vendor.full_name,
        },
    )

    # TRANSACTION: Vendor credit (escrowed)
    await producer.publish_message(
        service="wallet",
        operation="create_transaction",
        payload={
            "wallet_id": str(order.vendor_id),
            "tx_ref": str(order.tx_ref),
            "from_wallet_id": str(order.owner_id),
            "amount": str(total),
            "transaction_type": TransactionType.USER_TO_USER,
            "transaction_direction": TransactionDirection.CREDIT,
            "payment_status": PaymentStatus.ESCROWED,
            "payment_method": PaymentMethod.CARD,
            "from_user": customer.full_name or customer.business_name,
            "to_user": vendor.business_name or vendor.full_name,
        },
    )

    # STOCK DEDUCTION (already safe with rowcount check)
    if order.order_items:
        item = order.order_items[0].item
        qty = order.order_items[0].quantity
        result = await db.execute(
            update(Item)
            .where(Item.id == item.id, Item.stock >= qty)
            .values(stock=Item.stock - qty)
        )
        if result.rowcount == 0:
            logger.error(f"Stock deduction failed for item {item.id}")

    # CACHE CLEAR
    redis_client.delete(f"marketplace_user_orders:{order.owner_id}")
    redis_client.delete(f"marketplace_user_orders:{order.vendor_id}")
    redis_client.delete(f"marketplace_order_details:{order.id}")
    redis_client.delete(f"user_orders:{order.vendor_id}")
    redis_client.delete(f"user_orders:{order.owner_id}")

# ———————— Optional: Alert on partial failure ————————
async def trigger_payment_reconciliation_alert(order: Order, error: Exception):
    # Send to Slack, create DB ticket, email admin, etc.
    pass


async def pay_with_wallet(
    db: AsyncSession,
    order_id: UUID,
    customer: User,
) -> dict:
    """
    Handles pay with wallet with comprehensive error handling.

    Args:
        db: The database session.
        order_id: The ID of the order to pay.
        customer: The authenticated user making the purchase.

    Returns:
        A status object with payment status and balances.

    Raises:
        HTTPException: Various exceptions for validation errors.
    """
    try:
        # Fetch order with related items, vendor, and delivery in one efficient query
        stmt_order = (
            select(Order)
            .where(
                Order.id == order_id,
                Order.owner_id == customer.id,
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

        # Check if the order is already paid (idempotency)
        if order.order_payment_status == PaymentStatus.PAID:
            return {
                "payment_status": PaymentStatus.PAID,
                "message": "Order has already been paid.",
                "charged_amount": str(order.grand_total),
            }

        # Fetch customer wallet
        customer_wallet_stmt = (
            select(Wallet).where(Wallet.id == customer.id).with_for_update()
        )
        result = await db.execute(customer_wallet_stmt)
        customer_wallet = result.scalar_one_or_none()

        if not customer_wallet:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Buyer wallet not found"
            )

        charged_amount = 0

        # --- PACKAGE LOGIC ---
        if order.order_type == OrderType.PACKAGE:
            if not order.delivery or not order.delivery.delivery_fee:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Delivery fee required for package order.",
                )
            
            delivery_fee = order.delivery.delivery_fee
            charged_amount = delivery_fee
            
            if customer_wallet.balance < delivery_fee:
                # Release assigned rider
                order.delivery.rider_id = None  
                order.delivery.rider_phone_number = None
                order.delivery.dispatch_id = None

                await db.execute(update(User.has_delivery).where(User.id==order.delivery.rider_id).values(has_delivery=False))
                await db.commit()

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Insufficient funds in wallet. You can reassign the delivery to another rider when you make payment.",
                )

            try:
                # 1. Create debit transaction record
                await producer.publish_message(
                    service="wallet",
                    operation="create_transaction",
                    payload={
                        "wallet_id": str(customer_wallet.id),
                        "tx_ref": str(order.tx_ref),
                        "amount": str(delivery_fee),
                        "transaction_type": TransactionType.USER_TO_USER,
                        "payment_method": PaymentMethod.WALLET,
                        "transaction_direction": TransactionDirection.DEBIT,
                        "payment_status": PaymentStatus.PAID,
                        "from_user": customer.profile.full_name or customer.profile.business_name,
                        "to_user": "System Escrow",
                    },
                )

                # 2. Update customer wallet
                await producer.publish_message(
                    service="wallet",
                    operation="update_wallet",
                    payload={
                        "wallet_id": str(customer_wallet.id),
                        "balance_change": str(-delivery_fee),
                        "escrow_change": str(delivery_fee),
                    },
                )

                # 3. Update order payment status
                await producer.publish_message(
                    service="order_status",
                    operation="order_payment_status",
                    payload={"order_id": str(order.id), "new_status": PaymentStatus.PAID},
                )

                await db.commit()

            except Exception as e:
                await db.rollback()
                logger.error(f"Failed to process package payment for order {order_id}: {str(e)}")
                
                # Publish compensation/rollback messages if needed
                try:
                    await producer.publish_message(
                        service="order_status",
                        operation="order_payment_status",
                        payload={"order_id": str(order.id), "new_status": PaymentStatus.FAILED},
                    )
                except Exception as rollback_error:
                    logger.error(f"Failed to update order status to FAILED: {str(rollback_error)}")
                
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Payment processing failed. Please try again.",
                )

            # Notify customer on success
            try:
                customer_token = await get_user_notification_token(db=db, user_id=customer.id)
                if customer_token:
                    await send_push_notification(
                        tokens=[customer_token],
                        title="Payment Successful",
                        message=f"Your payment of ₦{delivery_fee} for delivery is successful.",
                        navigate_to="/(app)/delivery/orders",
                    )
            except Exception as notif_error:
                logger.warning(f"Failed to send notification: {str(notif_error)}")
                # Don't fail the whole transaction for notification errors

            # Clear caches
            try:
                redis_client.delete(f"user_related_orders:{customer.id}")
                redis_client.delete(f"user_orders:{order.owner_id}")
                redis_client.delete(f'user_orders:{order.delivery.rider_id}')
                redis_client.delete(f"user_orders:{order.delivery.dispatch_id}")
                redis_client.delete("paid_pending_deliveries")
                redis_client.delete("orders")
                redis_client.delete(f"order_by_id:{order.id}")
            except Exception as cache_error:
                logger.warning(f"Failed to clear cache: {str(cache_error)}")

            return {
                "payment_status": PaymentStatus.PAID,
                "charged_amount": str(delivery_fee),
            }

        # --- FOOD/LAUNDRY LOGIC ---
        vendor = await get_user_profile(order.vendor_id, db)
        total_price = order.grand_total
        charged_amount = total_price

        if customer_wallet.balance < total_price:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient funds in wallet",
            )

        try:
            # Update customer wallet (move to escrow)
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.owner_id),
                    "balance_change": str(-charged_amount),
                    "escrow_change": str(charged_amount),
                },
            )

            # Update vendor wallet (move to escrow)
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.vendor_id),
                    "balance_change": "0",
                    "escrow_change": str(order.grand_total),
                },
            )

            # Update order payment status
            await producer.publish_message(
                service="order_status",
                operation="order_payment_status",
                payload={"order_id": str(order.id), "new_status": PaymentStatus.PAID},
            )

            # Create customer transaction
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(order.owner_id),
                    "tx_ref": str(order.tx_ref),
                    "to_wallet_id": str(order.vendor_id),
                    "amount": str(charged_amount),
                    "transaction_type": TransactionType.USER_TO_USER,
                    "transaction_direction": TransactionDirection.DEBIT,
                    "payment_status": PaymentStatus.PAID,
                    "from_user": customer.profile.full_name or customer.profile.business_name,
                    "to_user": vendor.full_name or vendor.business_name,
                },
            )

            # Create vendor transaction
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(order.vendor_id),
                    "tx_ref": str(order.tx_ref),
                    "amount": str(order.amount_due_vendor),
                    "transaction_type": TransactionType.USER_TO_USER,
                    "transaction_direction": TransactionDirection.CREDIT,
                    "payment_status": PaymentStatus.PAID,
                    "from_user": customer.profile.full_name or customer.profile.business_name,
                    "to_user": vendor.full_name or vendor.business_name,
                },
            )

            await db.commit()

        except Exception as e:
            await db.rollback()
            if order.order_tyepe == OrderType.PACKAGE:
                # Release assigned rider
                order.delivery.rider_id = None  
                order.delivery.rider_phone_number = None
                order.delivery.dispatch_id = None

                await db.execute(update(User.has_delivery).where(User.id==order.delivery.rider_id).values(has_delivery=False))
                await db.commit()
            logger.error(f"Failed to process food/laundry payment for order {order_id}: {str(e)}")
            
            # Mark order as failed
            try:
                await producer.publish_message(
                    service="order_status",
                    operation="order_payment_status",
                    payload={"order_id": str(order.id), "new_status": PaymentStatus.FAILED},
                )
            except Exception as rollback_error:
                logger.error(f"Failed to update order status to FAILED: {str(rollback_error)}")
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Payment processing failed. Please try again. Your delivery assignment has been released; you can reassign when you make payment.",
            )

        # Send notifications (non-critical)
        try:
            await _send_notifications(db=db, order=order)
        except Exception as notif_error:
            logger.warning(f"Failed to send notifications: {str(notif_error)}")

        # Clear caches (non-critical)
        try:
            redis_client.delete(f"user_related_orders:{customer.id}")
            redis_client.delete(f"user_related_orders:{order.vendor_id}")
            redis_client.delete(f"user_orders:{order.owner_id}")
            redis_client.delete(f"user_orders:{order.vendor_id}")
            redis_client.delete("paid_pending_deliveries")
            redis_client.delete("orders")
            redis_client.delete(f"order_by_id:{order.id}")
        except Exception as cache_error:
            logger.warning(f"Failed to clear cache: {str(cache_error)}")

        return {
            "payment_status": PaymentStatus.PAID,
            "charged_amount": str(charged_amount),
        }

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error in pay_with_wallet for order {order_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during payment processing.",
        )

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
            logger.info(
                f"HTTP error occurred: {e.response.status_code} - {e.response.text}"
            )
            raise
        except httpx.RequestError as e:
            logger.info(f"Request error occurred: {e}")
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

    charge = await get_current_charge_settings(db)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if user.is_blocked:
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
            transaction_type=TransactionType.WITHDRAWAL,
            payment_method=PaymentMethod.BANK_TRANSFER,
            transaction_direction=TransactionDirection.DEBIT,
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

