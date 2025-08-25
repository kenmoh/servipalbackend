from typing import Dict, Any
from uuid import UUID

from sqlalchemy import insert, update, select

from app.database.database import get_db
from app.models.models import Delivery, Order
from app.queue.base_consumer import BaseQueueConsumer
from app.utils.logger_config import setup_logger
from app.config.config import redis_client


from app.queue.base_consumer import BaseQueueConsumer
from app.utils.utils import send_push_notification

logger = setup_logger()


class OrderStatusQueueConsumer(BaseQueueConsumer):
    def __init__(self):
        super().__init__("order_status", "order_status_updates")
        self._operation_handlers = {
            "update_order_status": self.process_order_status_update,
            "order_payment_status": self.process_order_payment_status_update,
        }

    async def process_order_status_update(self, payload: Dict[str, Any]):
        """Process order status update"""
        async for db in get_db():
            try:
                async with db.begin():
                    order_id = UUID(payload.get("order_id"))
                    order_status = payload.get("order_status")
                    delivery_status = payload.get("delivery_status")
                    delivery_id = payload.get("delivery_id")
                    cache_keys = payload.get('cache_keys', [])
                    notification_data = payload.get('notification_data', [])

                    result = await db.execute(
                        update(Order)
                        .where(Order.id == order_id)
                        .values(order_status=order_status)
                    )

                    if result.rowcount == 0:
                        raise ValueError(f"Order {order_id} not found")

                    if delivery_id:
                        result = await db.execute(
                            update(Delivery)
                            .where(Delivery.id == UUID(delivery_id))
                            .values(delivery_status=delivery_status)
                        )

                    for key in cache_keys:
                        redis_client.delete(key)

                    if notification_data:
                        for notification in notification_data:
                            await send_push_notification(**notification)
            except Exception as db_error:
                logger.error(f"Order status update error: {str(db_error)}")
                raise

    async def process_order_payment_status_update(self, payload: Dict[str, Any]):
        """Process order status update"""
        async for db in get_db():
            try:
                async with db.begin():
                    order_id = UUID(payload.get("order_id"))
                    new_status = payload.get("new_status")
                    order_status = payload.get('order_status')
                    result = await db.execute(
                        update(Order)
                        .where(Order.id == order_id)
                        .values(order_payment_status=new_status, order_status=order_status)
                    )

                    if result.rowcount == 0:
                        logger.error(f"ERROR updating order payment status")
                        raise ValueError(f"Order {order_id} not found")
            except Exception as db_error:
                logger.error(f"Order status update error: {str(db_error)}")
                raise


order_consumer = OrderStatusQueueConsumer()
