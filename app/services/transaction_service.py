import asyncio
import datetime
from uuid import UUID
import logging
from fastapi import BackgroundTasks, HTTPException, Request, status

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import ChargeAndCommission, Order, User, Wallet, Transaction, OrderItem
from app.schemas.marketplace_schemas import TopUpRequestSchema
from app.schemas.order_schema import OrderResponseSchema
from app.schemas.status_schema import PaymentStatus, TransactionType
from app.utils.logger_config import setup_logger
from app.utils.utils import (
    get_bank_code,
    get_fund_wallet_payment_link,
    transfer_money_to_user_account,
    verify_transaction_tx_ref,
)
from app.config.config import settings

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
        # Convert string to UUID if needed
        try:
            order_id = UUID(tx_ref)
        except (ValueError, TypeError):
            order_id = tx_ref
        
        # Get order with one efficient query
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
            and payload["amount"] == db_order.total_cost
            and payload["currency"] == "NGN"
            and db_order.payment_status != PaymentStatus.PAID
        ):
            # Verify transaction separately - moved outside the condition check
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
# async def handle_payment_webhook(
#     request: Request,
#     background_task: BackgroundTasks,
#     db: AsyncSession,
# ):
#     signature = request.headers.get("verif-hash")

#     if signature is None or signature != {settings.FLW_SECRET_HASH}:
#         raise HTTPException(status_code=401, detail="Unauthorized")

#     payload = await request.json()

#     db_order = await db.query(Order).filter(Order.id == payload["txRef"]).first()

#     result = await db.execute(select(Order).where*Order.id == payload["txRef"])

#     db_tranx = result.scalar_one_or_none()

#     if db_order is not None:
#         try:
#             if (
#                 payload["status"] == "successful"
#                 and payload["total_price"] == db_order.total_price
#                 and payload["amount"] == db_order.total_cost
#                 and payload["currency"] == "NGN"
#                 and verify_transaction_tx_ref(payload["txRef"])
#                 .get("data")
#                 .get("status")
#                 == "successful"
#                 and db_order.payment_status != PaymentStatus.PAID
#             ):
#                 # Update the database in the background with retry mechanism
#                 background_task.add_task(update_database, db_order, db)
#                 return {"message": "Success"}
#         except Exception as e:
#             logging.error(f"Error processing webhook: {e}")
#             raise HTTPException(
#                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 detail="INTERNAL_SERVER_ERROR",
#             )
#         return {"message": "Failed"}



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
                raise HTTPException(status_code=500, detail="Charge configuration not found")
            
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
                "amount_added": amount_to_add
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


# async def fund_wallet_callback(request: Request, db: AsyncSession):
#     tx_ref = await request.query_params["tx_ref"]
#     tx_status = await request.query_params["status"]

#     charge = db.query(Transaction).first()  # TODO: WORK ON THIS

#     stmt = select(Transaction).where(Transaction.id == tx_ref)
#     result = await db.execute(stmt)
#     transaction = result.scalar_one_or_none()

#     if (
#         tx_status == "successful"
#         and verify_transaction_tx_ref(tx_ref).get("data").get("status") == "successful"
#     ):
#         transaction.status = PaymentStatus.PAID
#         wallet = await get_wallet(transaction.wallet_id, db)

#         wallet.balance += (
#             (
#                 transaction.amount
#                 - (
#                     charge.payout_charge_transaction_upto_5000_naira
#                     * charge.value_added_tax
#                     + charge.payout_charge_transaction_upto_5000_naira
#                 )
#             )
#             if transaction.amount <= 5000
#             else (
#                 (
#                     transaction.amount
#                     - (
#                         # Remove FLW charge for amount <= NGN 5000
#                         charge.payout_charge_transaction_from_5001_to_50_000_naira
#                         * charge.value_added_tax
#                         + charge.payout_charge_transaction_from_5001_to_50_000_naira
#                     )
#                 )
#                 if transaction.amount > 5000 <= 50000
#                 else (
#                     transaction.amount
#                     - (
#                         # Remove FLW charge for amount > 5000 <= 50000
#                         charge.payout_charge_transaction_above_50_000_naira
#                         * charge.value_added_tax
#                         + charge.payout_charge_transaction_above_50_000_naira
#                     )
#                 )
#             )
#         )

#         db.add(transaction)
#         db.add(wallet)

#         await db.commit()
#         await db.refresh(transaction)
#         await db.refresh(wallet)

#     elif status == "cancelled":
#         transaction.payment_status = PaymentStatus.CANCELLED
#         await db.commit()
#     else:
#         transaction.payment_status = PaymentStatus.FAILED
#         await db.commit()
#     return {"payment_status": transaction.payment_status}


async def order_payment_callback(request: Request, db: AsyncSession):
    tx_ref = request.query_params["tx_ref"]
    tx_status = request.query_params["status"]
    
    # Ccheck if the transaction was successful
    verify_tranx = await verify_transaction_tx_ref(tx_ref)

    try:
        order_id = UUID(tx_ref)
    except (ValueError, TypeError):
        order_id = tx_ref
    
    # Determine the appropriate payment status
    if (tx_status == "successful" and verify_tranx.get('data', {}).get('status') == "successful"):
        new_status = PaymentStatus.PAID
    elif tx_status == "cancelled":
        new_status = PaymentStatus.CANCELLED
    else:
        new_status = PaymentStatus.FAILED
    
    # Update only the payment status and return it
    stmt = (
        update(Order)
        .where(Order.id == order_id)
        .values(order_payment_status=new_status)
        .returning(Order.order_payment_status)
    )
    
    result = await db.execute(stmt)
    await db.commit()
    
    # Get the updated status directly
    updated_status = result.scalar_one()
    
    return {"order_payment_status": updated_status}


async def pay_with_wallet(
    db: AsyncSession,
    order_id: UUID,
    buyer: User,
) -> OrderResponseSchema:
    """
    Handles the logic for a user buying a listed product with wallet funds.
    
    Args:
        db: The database session.
        order_id: The ID of the order to pay.
        buyer: The authenticated user making the purchase.
        
    Returns:
        The completed order with updated payment status.
        
    Raises:
        HTTPException: Various exceptions for validation errors.
    """
    async with db.begin():
        # Fetch order with related items and vendor in one efficient query
        stmt_order = (
            select(Order)
            .where(
                Order.id == order_id,
                Order.owner_id == buyer.id,
                Order.order_payment_status == PaymentStatus.PENDING
            )
            .options(
                selectinload(Order.order_items).selectinload(OrderItem.item),
                selectinload(Order.vendor),
            )
            .with_for_update()
        )
        result_order = await db.execute(stmt_order)
        order = result_order.scalar_one_or_none()
        
        # Handle order validation errors
        if not order:
            # Check why the order wasn't found to provide better error message
            check_stmt = select(Order).where(Order.id == order_id)
            check_result = await db.execute(check_stmt)
            check_order = check_result.scalar_one_or_none()
            
            if not check_order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, 
                    detail="Order not found"
                )
            elif check_order.owner_id != buyer.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not the owner of this order"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Order is not pending for payment"
                )

        # Fetch both wallets in parallel for better performance
        wallets_stmt = (
            select(Wallet)
            .where(Wallet.id.in_([buyer.id, order.vendor_id]))
            .with_for_update()
        )
        wallets_result = await db.execute(wallets_stmt)
        wallets = wallets_result.scalars().all()
        
        # Map wallets by ID for easy access
        wallets_by_id = {wallet.id: wallet for wallet in wallets}
        buyer_wallet = wallets_by_id.get(buyer.id)
        seller_wallet = wallets_by_id.get(order.vendor_id)
        
        # Validate wallets
        if not buyer_wallet:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="Buyer wallet not found"
            )
        if not seller_wallet:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="Seller wallet not found"
            )
        
        # Check if buyer has enough funds
        if buyer_wallet.balance < order.total_price:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient funds in wallet"
            )
        
        # Process the payment
        buyer_wallet.balance -= order.total_price
        seller_wallet.escrow_balance += order.total_price
        
        # Create transactions in bulk
        current_time = datetime.utcnow()
        transaction_values = [
            # Buyer transaction (DEBIT)
            {
                "id": uuid4(),
                "wallet_id": buyer_wallet.id,
                "amount": order.total_price,
                "transaction_type": TransactionType.DEBIT,
                "status": PaymentStatus.PAID,
                "created_at": current_time,
                "updated_at": current_time,
                "reference": f"ORDER-{order_id}-BUY"
            },
            # Seller transaction (CREDIT)
            {
                "id": uuid4(),
                "wallet_id": seller_wallet.id,
                "amount": order.total_price,
                "transaction_type": TransactionType.CREDIT,
                "status": PaymentStatus.PAID,
                "created_at": current_time,
                "updated_at": current_time,
                "reference": f"ORDER-{order_id}-SELL"
            }
        ]
        
        await db.execute(insert(Transaction), transaction_values)
        
        # Update order status
        order.order_payment_status = PaymentStatus.COMPLETED
        
        # No need to call commit explicitly within the async with db.begin() block
        # as it will automatically commit when the block exits
        
    # Refresh order outside the transaction for better performance
    await db.refresh(order)
    return order


# async def pay_with_wallet(
#     db: AsyncSession,
#     order_id: UUID,
#     buyer: User,
# ) -> OrderResponseSchema:
#     """
#     Handles the logic for a user buying a listed product.

#     Args:
#         db: The database session.
#         order_id: The ID of the order to pay.
#         buyer: The authenticated user making the purchase.

#     Returns:
#         The transaction record created for the purchase.

#     Raises:
#         HTTPException: Various exceptions for validation errors (not found, insufficient stock/funds, etc.).
#     """

#     async with db.begin():
#         # Fetch order with related items and vendor
#         stmt_order = (
#             select(Order)
#             .where(Order.id == order_id)
#             .options(
#                 selectinload(Order.order_items).selectinload(OrderItem.item),
#                 selectinload(Order.vendor),
#             )
#             .with_for_update()  # Lock the order row for update
#         )
#         result_order = await db.execute(stmt_order)
#         order = result_order.scalar_one_or_none()

#         if not order:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
#             )

#         if order.owner_id != buyer.id:
#             raise HTTPException(
#                 status_code=status.HTTP_403_FORBIDDEN,
#                 detail="You are not the owner of this order",
#             )

#         if order.order_payment_status != PaymentStatus.PENDING:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="Order is not pending for payment",
#             )

#         # Fetch Buyer's Wallet
#         buyer_wallet_stmt = (
#             select(Wallet).where(Wallet.id == buyer.id).with_for_update()
#         )
#         buyer_wallet_result = await db.execute(buyer_wallet_stmt)
#         buyer_wallet = buyer_wallet_result.scalar_one_or_none()

#         if not buyer_wallet:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND, detail="Buyer wallet not found"
#             )

#         if buyer_wallet.balance < order.total_price:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="Insufficient funds in wallet",
#             )
#         buyer_wallet.balance -= order.total_price

#         await db.execute(
#             insert(Transaction.__table__).values(
#                 wallet_id=buyer_wallet.id,
#                 amount=order.total_price,
#                 transaction_type=TransactionType.DEBIT,
#                 payment_status=PaymentStatus.PAID,
#             )
#         )

#         # Update the vendor's escrow balance
#         seller_wallet_stmt = (
#             select(Wallet).where(Wallet.id ==
#                                  order.vendor_id).with_for_update()
#         )
#         seller_wallet_result = await db.execute(seller_wallet_stmt)
#         seller_wallet = seller_wallet_result.scalar_one_or_none()
#         seller_wallet.escrow_balance += order.total_price

#         await db.execute(
#             insert(Transaction.__table__).values(
#                 wallet_id=seller_wallet.id,
#                 amount=order.total_price,
#                 transaction_type=TransactionType.CREDIT,
#                 payment_status=PaymentStatus.PAID,
#             )
#         )

#         # Create the transaction for buyer
#         await db.commit()

#         order.order_payment_status = PaymentStatus.COMPLETED

#         await db.refresh(order)
#         return order


async def make_withdrawal(db: AsyncSession, current_user: User) -> dict:
    """Process withdrawal of entire wallet balance"""

    async with db.begin():
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
                charge=charge,
                description=f"Withdrawal to {user.profile.bank_name} - {user.profile.bank_account_number}",
            )
            db.add(withdrawal)
            await db.flush()

            # Get bank code and initiate transfer
            bank_code = await get_bank_code(user.profile.bank_name)
            previous_balance = user.wallet.balance

            transfer_response = await transfer_money_to_user_account(
                bank_code=bank_code,
                amount=str(previous_balance),
                narration=f"Wallet withdrawal of ₦ {previous_balance:,.2f}",
                reference=str(withdrawal.id),
                account_number=user.profile.bank_account_number,
                beneficiary_name=user.profile.account_holder_name,
            )

            if transfer_response.get("status") == "success":
                # Update wallet balance
                user.wallet.balance = 0
                withdrawal.payment_status = PaymentStatus.PAID

                await db.commit()

                return {
                    "status": "success",
                    "message": "Withdrawal processed successfully",
                    "transaction_id": withdrawal.id,
                    "amount": previous_balance,
                    "bank_name": user.profile.bank_name,
                    "account_number": user.profile.bank_account_number,
                    "beneficiary": user.profile.full_name
                    if user.profile.full_name
                    else user.profile.business_name,
                    "timestamp": withdrawal.created_at,
                }
            elif transfer_response.get("status") in ["cancel", "failed"]:
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


# def make_withdrawal(db: Session, user: User):
#     wallet = db.query(Wallet).filter(Wallet.user_id == user["id"]).first()
#     charge = db.query(ChargeAndCommission).first()
#     if wallet.balance <= 0:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient fund")

#     if user["is_suspended"]:
#         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
#                            detail="Your account is suspended. Please contact support.")
#     if not user["bank_account_number"]:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
#                             detail="Please update your profile with your account number.")

#     withdrawal = Transaction(
#         user_id=user["id"],
#         wallet_id=user["wallet_id"],
#         name=user["company_name"] if user["company_name"] else user["username"],
#         amount=wallet.balance,
#         status=PaymentStatus.PENDING,
#         transaction_type=TransactionType.DEBIT,
#         created_at=datetime.now(),
#     )
#     db.add(withdrawal)
#     db.flush()

#     wallet.user_id = user["id"]
#     wallet.company_name = user["company_name"] or None
#     wallet.username = user["username"] or None
#     wallet.balance -= withdrawal.amount

#     db.add(withdrawal)
#     db.commit()
#     db.refresh(withdrawal)

#     db.refresh(wallet)

#     bank_code = get_bank_code(user["bank_name"])
#     response = transfer_money_to_user_account(
#         bank_code=bank_code,
#         charge=charge,
#         amount=str(wallet.balance),
#         narration=f"Withdrawal of ₦ {wallet.balance} from your wallet was successful.",
#         reference=user["wallet_id"],
#         account_number=user["bank_account_number"],
#         beneficiary_name=(
#             user["account_holder_name"]
#             if user["account_holder_name"]
#             else user["company_name"]
#         ),
#     )

#     if response.get("status") == "success":
#         withdrawal.status = PaymentStatus.SUCCESSFUL
#         db.commit()
#         db.refresh(withdrawal)

#     return {
#         "status": response.get("status"),
#         "message": response.get("message"),
#         "amount": response.get("data").get("amount"),
#         "created_at": response.get("data").get("created_at"),
#     }
