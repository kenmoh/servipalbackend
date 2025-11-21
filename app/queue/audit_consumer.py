from decimal import Decimal
from typing import Dict, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_db
from app.models.models import TransactionLog
from app.queue.base_consumer import BaseQueueConsumer
from app.utils.logger_config import setup_logger
from app.config.config import redis_client

logger = setup_logger()


class AuditQueueConsumer(BaseQueueConsumer):
    def __init__(self):
        super().__init__("audit", "audit_logs")
        self._operation_handlers = {
            "create_transaction_log": self.process_create_transaction_log,
        }

    def _check_idempotency(self, idempotency_key: str) -> bool:
        """Check if operation has already been processed."""
        if not idempotency_key:
            return False
        cache_key = f"audit_idempotency:{idempotency_key}"
        return redis_client.get(cache_key) is not None

    def _set_idempotency(self, idempotency_key: str, ttl: int = 86400) -> None:
        """Mark operation as processed."""
        if not idempotency_key:
            return
        cache_key = f"audit_idempotency:{idempotency_key}"
        redis_client.setex(cache_key, ttl, "1")

    async def process_create_transaction_log(self, payload: Dict[str, Any]):
        """Process transaction log creation with idempotency."""
        # Use order_id as idempotency key to prevent duplicate logs
        idempotency_key = f"transaction_log:{payload.get('order_id')}:{payload.get('action')}"
        
        if self._check_idempotency(idempotency_key):
            logger.info(f"Skipping duplicate transaction log: {idempotency_key}")
            return

        async for db in get_db():
            try:
                async with db.begin():
                    vendor_id = UUID(payload.get("vendor_id"))
                    user_id = UUID(payload.get("user_id"))
                    order_id = UUID(payload.get("order_id"))
                    amount = Decimal(payload.get("amount"))
                    action = payload.get("action")
                    status = payload.get("status")
                    details = payload.get("details", {})

                    logger.info(
                        f"Creating transaction log: order_id={order_id}, "
                        f"vendor_id={vendor_id}, action={action}"
                        f"user_id={user_id}, action={action}"
                    )

                    # Check if log already exists
                    from sqlalchemy import select
                    existing_log = await db.scalar(
                        select(TransactionLog).where(
                            TransactionLog.order_id == order_id,
                            TransactionLog.action == action,
                        )
                    )

                    if existing_log:
                        logger.info(f"Transaction log already exists for order {order_id}, action {action}")
                        self._set_idempotency(idempotency_key)
                        return

                    # Create the log
                    transaction_log = TransactionLog(
                        vendor_id=vendor_id,
                        order_id=order_id,
                        user_id=user_id,
                        amount=amount,
                        action=action,
                        status=status,
                        details=details,
                    )
                    
                    db.add(transaction_log)
                    await db.commit()

                    # Mark as processed
                    self._set_idempotency(idempotency_key)

                    logger.info(
                        f"✓ Successfully created transaction log for order {order_id}"
                    )

            except Exception as e:
                await db.rollback()
                logger.error(
                    f"Failed to create transaction log: {str(e)}", 
                    exc_info=True
                )
                raise