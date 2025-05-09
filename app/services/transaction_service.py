import asyncio
from uuid import UUID
import logging
from fastapi import BackgroundTasks, HTTPException, Request, status

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


from app.models.models import Order, User, Wallet, Transaction, OrderItem
from app.schemas.marketplace_schemas import TopUpRequestSchema
from app.schemas.order_schema import OrderResponseSchema
from app.schemas.status_schema import PaymentStatus, TransactionType
from app.utils.utils import (
    get_fund_wallet_payment_link,
    verify_transaction_tx_ref,
)
from app.config.config import settings


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
            print(f"Error updating database: {e}")
            await asyncio.sleep(1)


async def top_up_wallet(
    db: AsyncSession, current_user: User, topup_data: TopUpRequestSchema
) -> TopUpRequestSchema:
    """
    Initiates a wallet top-up transaction.

    Args:
        db: The database session.
        current_user: The user initiating the top-up.
        topup_data: The top-up request data (amount).

    Returns:
        Details of the initiated transaction, including the payment link.

    Raises:
        HTTPException: If the user's wallet is not found.
    """
    # 1. Get user's wallet (Wallet ID is same as User ID in your model)
    wallet = await db.get(Wallet, current_user.id)
    if not wallet:
        try:
            # Consider creating a wallet automatically if it doesn't exist,
            wallet = Wallet(id=current_user.id, balance=0, escrow_balance=0)
            db.add(wallet)
            await db.commit()
            await db.refresh(wallet)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Failed to create a wallet for the user. {e}",
            )

    # 3. Create the transaction record
    transaction = Transaction(
        wallet_id=wallet.id,
        amount=topup_data.amount,
        transaction_type=TransactionType.CREDIT,
        payment_status=PaymentStatus.PENDING,
    )
    db.add(transaction)
    await db.flush(transaction)

    try:
        # Generate payment link *after* flushing to get the transaction ID
        payment_link = await get_fund_wallet_payment_link(
            id=transaction.id, amount=transaction.amount, current_user=current_user
        )
        transaction.payment_link = payment_link

        # 4. Save transaction with payment link
        await db.commit()
        await db.refresh(transaction)
    except Exception as e:
        # If payment link generation fails, rollback the transaction creation
        await db.rollback()
        # Log the error e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate payment link: {e}",
        )
    # 4. Save to DB
    await db.commit()
    await db.refresh(transaction)

    return TopUpRequestSchema.model_validate(transaction)


# --- Webhook Handler ---


# SUCCESS WEBHOOK
async def handle_payment_webhook(
    request: Request,
    background_task: BackgroundTasks,
    db: AsyncSession,
):
    signature = request.headers.get("verif-hash")

    if signature is None or signature != {settings.FLW_SECRET_HASH}:
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = await request.json()

    db_order = await db.query(Order).filter(Order.id == payload["txRef"]).first()

    db_tranx = (
        await db.query(Transaction).filter(Transaction.id == payload["txRef"]).first()
    )

    if db_order:
        try:
            if (
                payload["status"] == "successful"
                and payload["total_price"] == db_order.total_price
                and payload["amount"] == db_order.total_cost
                and payload["currency"] == "NGN"
                and verify_transaction_tx_ref(payload["txRef"])
                .get("data")
                .get("status")
                == "successful"
                and db_order.payment_status != PaymentStatus.PAID
            ):
                # Update the database in the background with retry mechanism
                background_task.add_task(update_database, db_order, db)
                return {"message": "Success"}
        except Exception as e:
            logging.error(f"Error processing webhook: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="INTERNAL_SERVER_ERROR",
            )
        return {"message": "Failed"}

    elif db_tranx:
        try:
            if (
                payload["status"] == "successful"
                and payload["amount"] == db_tranx.total_cost
                and payload["amount"] == db_tranx.amount
                and payload["currency"] == "NGN"
                and verify_transaction_tx_ref(payload["txRef"])
                .get("data")
                .get("status")
                == "successful"
                and db_tranx.payment_status != PaymentStatus.PAID
            ):
                background_task.add_task(update_database, db_tranx, db)
                return {"message": "Success"}
        except Exception as e:
            logging.error(f"Error processing webhook: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="INTERNAL_SERVER_ERROR",
            )
        return {"message": "Failed"}


async def fund_wallet_callback(request: Request, db: AsyncSession):
    tx_ref = await request.query_params["tx_ref"]
    tx_status = await request.query_params["status"]

    charge = db.query(Transaction).first()  # TODO: WORK ON THIS

    stmt = select(Transaction).where(Transaction.id == tx_ref)
    result = await db.execute(stmt)
    transaction = result.scalar_one_or_none()

    if (
        tx_status == "successful"
        and verify_transaction_tx_ref(tx_ref).get("data").get("status") == "successful"
    ):
        transaction.status = PaymentStatus.PAID
        wallet = await get_wallet(transaction.wallet_id, db)

        wallet.balance += (
            (
                transaction.amount
                - (
                    charge.payout_charge_transaction_upto_5000_naira
                    * charge.value_added_tax
                    + charge.payout_charge_transaction_upto_5000_naira
                )
            )
            if transaction.amount <= 5000
            else (
                (
                    transaction.amount
                    - (
                        # Remove FLW charge for amount <= NGN 5000
                        charge.payout_charge_transaction_from_5001_to_50_000_naira
                        * charge.value_added_tax
                        + charge.payout_charge_transaction_from_5001_to_50_000_naira
                    )
                )
                if transaction.amount > 5000 <= 50000
                else (
                    transaction.amount
                    - (
                        # Remove FLW charge for amount > 5000 <= 50000
                        charge.payout_charge_transaction_above_50_000_naira
                        * charge.value_added_tax
                        + charge.payout_charge_transaction_above_50_000_naira
                    )
                )
            )
        )

        db.add(transaction)
        db.add(wallet)

        await db.commit()
        await db.refresh(transaction)
        await db.refresh(wallet)

    elif status == "cancelled":
        transaction.payment_status = PaymentStatus.CANCELLED
        await db.commit()
    else:
        transaction.payment_status = PaymentStatus.FAILED
        await db.commit()
    return {"payment_status": transaction.payment_status}


async def order_payment_callback(request: Request, db: AsyncSession):
    tx_ref = await request.query_params["tx_ref"]
    tx_status = await request.query_params["status"]

    stmt = select(Order).where(Order.id == tx_ref)
    result = await db.execute(stmt)
    db_order = result.scalar_one_or_none()

    if (
        tx_status == "successful"
        and verify_transaction_tx_ref(tx_ref).get("data").get("status") == "successful"
    ):
        db_order.payment_status = PaymentStatus.PAID
        await db.commit()
        return {"payment_status": db_order.payment_status}

    if tx_status == "cancelled":
        db_order.payment_status = PaymentStatus.CANCELLED
        await db.commit()
        return {"payment_status": db_order.payment_status}

    else:
        db_order.payment_status = PaymentStatus.FAILED
        await db.commit()
        return {"payment_status": db_order.payment_status}


async def pay_with_wallet(
    db: AsyncSession,
    order_id: UUID,
    buyer: User,
) -> OrderResponseSchema:
    """
    Handles the logic for a user buying a listed product.

    Args:
        db: The database session.
        order_id: The ID of the order to pay.
        buyer: The authenticated user making the purchase.

    Returns:
        The transaction record created for the purchase.

    Raises:
        HTTPException: Various exceptions for validation errors (not found, insufficient stock/funds, etc.).
    """

    async with db.begin():
        # Fetch order with related items and vendor
        stmt_order = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.order_items).selectinload(OrderItem.item),
                selectinload(Order.vendor),
            )
            .with_for_update()  # Lock the order row for update
        )
        result_order = await db.execute(stmt_order)
        order = result_order.scalar_one_or_none()

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
            )

        if order.owner_id != buyer.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not the owner of this order",
            )

        if order.order_payment_status != PaymentStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order is not pending for payment",
            )

        # Fetch Buyer's Wallet
        buyer_wallet_stmt = (
            select(Wallet).where(Wallet.id == buyer.id).with_for_update()
        )
        buyer_wallet_result = await db.execute(buyer_wallet_stmt)
        buyer_wallet = buyer_wallet_result.scalar_one_or_none()

        if not buyer_wallet:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Buyer wallet not found"
            )

        if buyer_wallet.balance < order.total_price:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient funds in wallet",
            )
        buyer_wallet.balance -= order.total_price

        await db.execute(
            insert(Transaction.__table__).values(
                wallet_id=buyer_wallet.id,
                amount=order.total_price,
                transaction_type=TransactionType.DEBIT,
                payment_status=PaymentStatus.PAID,
            )
        )

        # Update the vendor's escrow balance
        seller_wallet_stmt = (
            select(Wallet).where(Wallet.id ==
                                 order.vendor_id).with_for_update()
        )
        seller_wallet_result = await db.execute(seller_wallet_stmt)
        seller_wallet = seller_wallet_result.scalar_one_or_none()
        seller_wallet.escrow_balance += order.total_price

        await db.execute(
            insert(Transaction.__table__).values(
                wallet_id=seller_wallet.id,
                amount=order.total_price,
                transaction_type=TransactionType.CREDIT,
                payment_status=PaymentStatus.PAID,
            )
        )

        # Create the transaction for buyer
        await db.commit()

        order.order_payment_status = PaymentStatus.COMPLETED

        await db.refresh(order)
        return order
