from decimal import Decimal
from typing import Dict, Any
from uuid import UUID

from pydantic import UUID1
from sqlalchemy import insert, update, select
from sqlalchemy.ext.asyncio import AsyncSession


from app.database.database import get_db
from app.models.models import Transaction, Wallet
from app.queue.base_consumer import BaseQueueConsumer
from app.utils.logger_config import setup_logger

logger = setup_logger()


class WalletQueueConsumer(BaseQueueConsumer):
    def __init__(self):
        super().__init__("wallet", "wallet_updates")
        self._operation_handlers = {
            "update_wallet": self.process_wallet_update,
            "update_transaction": self.process_transaction_update,
            "create_transaction": self.process_create_transaction,
        }

    async def _safe_wallet_update(
        self,
        db: AsyncSession,
        wallet_id: UUID,
        balance_change: Decimal,
        escrow_change: Decimal,
    ) -> None:
        """Perform atomic wallet update"""
        # Get wallet with row lock for update
        # stmt = "SELECT * FROM wallets WHERE id = :wallet_id FOR UPDATE"
        result = await db.execute(
            select(Wallet).where(Wallet.id == wallet_id).with_for_update()
        )
        # result = await db.execute(stmt, {"wallet_id": wallet_id})
        wallet = result.scalar_one_or_none()

        if not wallet:
            raise ValueError(f"Wallet {wallet_id} not found")

        # Calculate new balances
        new_balance = wallet.balance + balance_change
        new_escrow = wallet.escrow_balance + escrow_change

        # Validate balances
        if new_balance < 0:
            raise ValueError(
                f"Insufficient balance: {wallet.balance} available, {abs(balance_change)} needed"
            )
        if new_escrow < 0:
            raise ValueError(
                f"Insufficient escrow balance: {wallet.escrow_balance} available, {abs(escrow_change)} needed"
            )

        # Update wallet
        wallet.balance = new_balance
        wallet.escrow_balance = new_escrow

    async def process_wallet_update(self, payload: Dict[str, Any]):
        """Process wallet balance update"""
        async for db in get_db():
            try:
                async with db.begin():
                    wallet_id = payload.get("wallet_id")
                    balance_change = payload.get("balance_change", 0)
                    escrow_change = payload.get("escrow_change", 0)

                    await self._safe_wallet_update(
                        db=db,
                        wallet_id=wallet_id,
                        balance_change=Decimal(balance_change),
                        escrow_change=Decimal(escrow_change),
                    )
            except Exception as db_error:
                logger.error(f"Wallet update error: {str(db_error)}")
                raise


    # async def process_wallet_update(self, payload: Dict[str, Any]):
    #     """Process wallet balance update with idempotency protection"""
    #     async for db in get_db():
    #         try:
    #             async with db.begin():
    #                 wallet_id = payload.get("wallet_id")
    #                 balance_change = payload.get("balance_change", 0)
    #                 escrow_change = payload.get("escrow_change", 0)
    #                 idempotency_key = payload.get("idempotency_key")
                    
    #                 # IDEMPOTENCY CHECK: Prevent duplicate wallet updates
    #                 if idempotency_key:
    #                     processed_key = f"wallet_update:{idempotency_key}"
                        
    #                     # Check if this operation was already processed
    #                     if redis_client.get(processed_key):
    #                         logger.info(f"Wallet update already processed: {idempotency_key}")
    #                         return {
    #                             "status": "already_processed",
    #                             "message": f"Wallet update for {idempotency_key} already completed"
    #                         }
                        
    #                     # Mark as processing (with short TTL to handle failures)
    #                     redis_client.setex(f"{processed_key}:processing", 60, "processing")
                    
    #                 # Perform the wallet update
    #                 result = await self._safe_wallet_update(
    #                     db=db,
    #                     wallet_id=wallet_id,
    #                     balance_change=Decimal(balance_change),
    #                     escrow_change=Decimal(escrow_change),
    #                 )
                    
    #                 # Mark operation as completed after successful update
    #                 if idempotency_key:
    #                     redis_client.setex(processed_key, 3600, "completed")  # 1 hour TTL
    #                     redis_client.delete(f"{processed_key}:processing")  # Clean up processing flag
                    
    #                 logger.info(f"Wallet update completed for wallet {wallet_id}: balance_change={balance_change}, escrow_change={escrow_change}")
    #                 return {
    #                     "status": "completed",
    #                     "wallet_id": wallet_id,
    #                     "balance_change": str(balance_change),
    #                     "escrow_change": str(escrow_change)
    #                 }
                    
    #         except Exception as db_error:
    #             # Clean up processing flag on error
    #             if idempotency_key:
    #                 redis_client.delete(f"wallet_update:{idempotency_key}:processing")
                
    #             logger.error(f"Wallet update error: {str(db_error)}")
    #             raise

    async def process_create_transaction(self, payload: Dict[str, Any]):
        """Process transaction creation"""
        async for db in get_db():
            try:
                async with db.begin():
                    wallet_id = payload.get("wallet_id")
                    tx_ref = payload.get("tx_ref")
                    to_wallet_id = payload.get("to_wallet_id", None)
                    amount = payload.get("amount")
                    transaction_type = payload.get("transaction_type")
                    transaction_direction = payload.get("transaction_direction")
                    payment_status = payload.get("payment_status")
                    payment_method = payload.get("payment_method")
                    from_user = payload.get("from_user")
                    to_user = payload.get("to_user")

                    await db.execute(
                        insert(Transaction).values(
                            wallet_id=UUID(wallet_id),
                            to_wallet_id=UUID(to_wallet_id)
                            if to_wallet_id is not None
                            else None,
                            tx_ref=UUID1(tx_ref),
                            amount=Decimal(amount),
                            transaction_type=transaction_type,
                            transaction_direction=transaction_direction,
                            payment_status=payment_status,
                            from_user=from_user,
                            payment_method=payment_method,
                            to_user=to_user,
                        )
                    )

            except Exception as db_error:
                logger.error(f"Transaction creation error: {str(db_error)}")
                raise

    # async def process_create_transaction(self, payload: Dict[str, Any]):
    #     """Process transaction creation with idempotency protection"""
    #     async for db in get_db():
    #         try:
    #             async with db.begin():
    #                 wallet_id = payload.get("wallet_id")
    #                 tx_ref = payload.get("tx_ref")
    #                 to_wallet_id = payload.get("to_wallet_id", None)
    #                 amount = payload.get("amount")
    #                 transaction_type = payload.get("transaction_type")
    #                 transaction_direction = payload.get("transaction_direction")
    #                 payment_status = payload.get("payment_status")
    #                 payment_method = payload.get("payment_method")
    #                 from_user = payload.get("from_user")
    #                 to_user = payload.get("to_user")
    #                 idempotency_key = payload.get("idempotency_key") 

    #                 # Convert to proper types for comparison
    #                 wallet_uuid = UUID(wallet_id)
    #                 tx_ref_uuid = UUID1(tx_ref)
    #                 to_wallet_uuid = UUID(to_wallet_id) if to_wallet_id else None
                    
    #                 # IDEMPOTENCY CHECK: Look for existing transaction
    #                 existing_tx_conditions = [
    #                     Transaction.wallet_id == wallet_uuid,
    #                     Transaction.tx_ref == tx_ref_uuid,
    #                     Transaction.transaction_direction == transaction_direction,
    #                     Transaction.amount == Decimal(amount)
    #                 ]
                    
    #                 # If idempotency key is provided, use it as primary check
    #                 if idempotency_key:
    #                     # First check by idempotency key if you have a column for it
    #                     existing_tx_conditions.append(Transaction.idempotency_key == idempotency_key)
                        
                    
    #                 # If to_wallet_id is provided, include it in the check
    #                 if to_wallet_uuid:
    #                     existing_tx_conditions.append(Transaction.to_wallet_id == to_wallet_uuid)
                    
    #                 # Check for existing transaction
    #                 existing_tx_stmt = select(Transaction).where(and_(*existing_tx_conditions))
    #                 result = await db.execute(existing_tx_stmt)
    #                 existing_transaction = result.scalar_one_or_none()
                    
    #                 if existing_transaction:
    #                     logger.info(f"Transaction already exists for tx_ref: {tx_ref}, wallet: {wallet_id}")
    #                     return {
    #                         "status": "already_exists",
    #                         "transaction_id": str(existing_transaction.id),
    #                         "message": "Transaction already processed"
    #                     }
                    
    #                 # Create new transaction if it doesn't exist
    #                 transaction_values = {
    #                     "wallet_id": wallet_uuid,
    #                     "to_wallet_id": to_wallet_uuid,
    #                     "tx_ref": tx_ref_uuid,
    #                     "amount": Decimal(amount),
    #                     "transaction_type": transaction_type,
    #                     "transaction_direction": transaction_direction,
    #                     "payment_status": payment_status,
    #                     "from_user": from_user,
    #                     "payment_method": payment_method,
    #                     "to_user": to_user,
    #                 }
                    
    #                 # Add idempotency key if provided (requires adding column to Transaction model)
    #                 if idempotency_key:
    #                     transaction_values["idempotency_key"] = idempotency_key
                    
    #                 await db.execute(insert(Transaction).values(**transaction_values))
                    
    #                 logger.info(f"Created new transaction for tx_ref: {tx_ref}, wallet: {wallet_id}")
    #                 return {
    #                     "status": "created",
    #                     "message": "Transaction created successfully"
    #                 }
                    
    #         except IntegrityError as integrity_error:
    #             # Handle unique constraint violations (if you have unique constraints on tx_ref + wallet_id)
    #             logger.warning(f"Transaction already exists (integrity error): {str(integrity_error)}")
    #             return {
    #                 "status": "already_exists",
    #                 "message": "Transaction already processed (integrity constraint)"
    #             }
    #         except Exception as db_error:
    #             logger.error(f"Transaction creation error: {str(db_error)}")
    #             raise

    async def process_transaction_update(self, payload: Dict[str, Any]):
        """Process transaction update"""
        async for db in get_db():
            try:
                async with db.begin():
                    wallet_id = UUID(payload.get("wallet_id"))
                    tx_ref = UUID1(payload.get("tx_ref"))
                    to_user = payload.get("to_user")
                    payment_status = payload.get("payment_status")
                    payment_method = payload.get("payment_method")
                    transaction_direction = payload.get("transaction_direction")
                    is_fund_wallet = payload.get("is_fund_wallet")

                    if is_fund_wallet:
                        await db.execute(
                            update(Transaction)
                            .where(
                                Transaction.wallet_id == wallet_id,
                                Transaction.tx_ref == tx_ref,
                            )
                            .values(
                                to_user="Self",
                                payment_method=payment_method,
                                payment_status=payment_status,
                                transaction_direction=transaction_direction,
                            )
                        )
                    await db.execute(
                        update(Transaction)
                        .where(
                            Transaction.wallet_id == wallet_id,
                            Transaction.tx_ref == tx_ref,
                        )
                        .values(to_user=to_user)
                    )
            except Exception as db_error:
                logger.error(f"Transaction update error: {str(db_error)}")
                raise
