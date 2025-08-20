from typing import Dict, Any

from app.queue.base_consumer import BaseQueueConsumer


class OrderStatusQueueConsumer(BaseQueueConsumer):
    def __init__(self):
        super().__init__("order_status", "order_status_updates")
        self._operation_handlers = {
            "update_order_status": self.process_order_status_update,
            # "create_order": self.process_create_order,
        }

    async def process_order_status_update(self, payload: Dict[str, Any]):
        """Process order status update"""
        async for db in get_db():
            try:
                async with db.begin():
                    order_id = UUID(payload.get('order_id'))
                    new_status = payload.get('new_status')
                    delivery_id = payload.get('delivery_id')
                    notification_data = payload.get('notification', {})
                    cache_keys = payload.get('cache_keys', [])
                    
                    result = await db.execute(
                        update(Order)
                        .where(Order.id == order_id)
                        .values(order_status=new_status)
                    )
                    
                    if result.rowcount == 0:
                        raise ValueError(f"Order {order_id} not found")
                    
                    if delivery_id:
                        result = await db.execute(
                            update(Delivery)
                            .where(Delivery.id == UUID(delivery_id))
                            .values(delivery_status=new_status)
                        )
                    
                    for key in cache_keys:
                        redis_client.delete(key)
                    
                    if notification_data:
                        await notification_queue.publish_notification(**notification_data)
            except Exception as db_error:
                logger.error(f"Order status update error: {str(db_error)}")
                raise

    

order_consumer = OrderStatusQueueConsumer()