import asyncio
import json
from datetime import datetime
from uuid import UUID
from typing import Optional, Dict, Any
from aio_pika import connect_robust, Message, DeliveryMode, IncomingMessage, ExchangeType
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.models import Delivery, Order
from app.database.database import get_db
from app.utils.logger_config import setup_logger
from app.config.config import settings, redis_client
from app.schemas.status_schema import OrderStatus
from app.queue.notification_queue import notification_queue

logger = setup_logger()

class OrderStatusQueue:
    def __init__(self):
        self._connection = None
        self._channel = None
        self._exchange = None
        self._consuming = False
        self._consumer_task = None

    async def connect(self):
        """Establish connection to RabbitMQ"""
        if not self._connection:
            self._connection = await connect_robust(settings.RABBITMQ_URL)
            self._channel = await self._connection.channel()
            self._exchange = await self._channel.declare_exchange(
                "order_operations",
                ExchangeType.DIRECT,
                durable=True
            )
            
            # Declare queue
            self._queue = await self._channel.declare_queue(
                "order_status_updates",
                durable=True,
                arguments={
                    'x-message-ttl': 1000 * 60 * 60 * 24,  # 24 hours
                    'x-dead-letter-exchange': 'order_dlx',
                    'x-dead-letter-routing-key': 'failed_order_updates'
                }
            )
            
            # Declare DLQ for failed updates
            self._dlq = await self._channel.declare_queue(
                "failed_order_updates",
                durable=True
            )
            
            await self._queue.bind(self._exchange, routing_key="order_update")

    async def process_order_update(self, message: IncomingMessage):
        """Process order status update message"""
        try:
            data = json.loads(message.body.decode())
            
            order_id = data.get('order_id')
            delivery_id = data.get('delivery_id' or None)
            new_status = data.get('new_status')
            notification_data = data.get('notification', {})
            cache_keys = data.get('cache_keys', [])
            
            if not order_id or not new_status:
                raise ValueError("Missing required order update fields")
            
            async for db in get_db():
                try:
                    # Start transaction
                    async with db.begin():
                        # Update order status
                        result = await db.execute(
                            update(Order)
                            .where(Order.id == UUID(order_id))
                            .values(order_status=new_status)
                        )
                        
                        if result.rowcount == 0:
                            raise ValueError(f"Order {order_id} not found")
                        
                        # Clear relevant cache keys
                        for key in cache_keys:
                            redis_client.delete(key)
                        
                        # Send notification if provided
                        if notification_data:
                            await notification_queue.publish_notification(**notification_data)

                        if delivery_id:
                            result = await db.execute(
                                update(Delivery)
                                .where(Delivery.id == UUID(delivery_id))
                                .values(delivery_status=new_status)
                            )

                    # If we got here, update was successful
                    await message.ack()
                    logger.info(f"Successfully updated order {order_id} to status {new_status}")
                    
                except Exception as db_error:
                    logger.error(f"Database error processing order update: {str(db_error)}")
                    await message.reject(requeue=True)
                    
        except Exception as e:
            logger.error(f"Error processing order update: {str(e)}")
            # If this is already a retry, send to DLQ
            if message.header.delivery_count > 3:
                await message.reject(requeue=False)
            else:
                await message.reject(requeue=True)

    async def publish_order_update(
        self,
        order_id: str,
        new_status: OrderStatus,
        delivery_id: str = None,
        notification_data: Optional[Dict[str, Any]] = None,
        cache_keys: Optional[list[str]] = None
    ):
        """Publish order status update message"""
        try:
            await self.connect()
            
            update_data = {
                "order_id": order_id,
                "delivery_id": delivery_id,
                "new_status": new_status,
                "notification": notification_data,
                "cache_keys": cache_keys or [],
                "timestamp": datetime.now().isoformat()
            }
            
            message = Message(
                json.dumps(update_data).encode(),
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type='application/json'
            )
            
            await self._exchange.publish(
                message,
                routing_key="order_update"
            )
            logger.info(f"Published status update for order {order_id}")
            
        except Exception as e:
            logger.error(f"Failed to publish order update: {str(e)}")
            raise

    async def start_consuming(self):
        """Start consuming messages"""
        if self._consuming:
            return
            
        await self.connect()
        self._consuming = True
        
        async def _consume():
            try:
                async with self._queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        await self.process_order_update(message)
            except Exception as e:
                logger.error(f"Consumer error: {str(e)}")
                self._consuming = False
        
        self._consumer_task = asyncio.create_task(_consume())
        logger.info("Started order status consumer")

    async def stop_consuming(self):
        """Stop consuming messages"""
        if self._consumer_task:
            self._consumer_task.cancel()
            self._consuming = False
        
        if self._connection:
            await self._connection.close()
            self._connection = None
            self._channel = None
            self._exchange = None

# Global instance
order_status_queue = OrderStatusQueue()

# Start consumer when application starts
async def start_order_status_consumer():
    await order_status_queue.start_consuming()

# Stop consumer when application stops
async def stop_order_status_consumer():
    await order_status_queue.stop_consuming()
