import asyncio
from typing import List, Optional, Dict, Any
import json
from datetime import datetime
from aio_pika import connect_robust, Message, DeliveryMode, IncomingMessage, ExchangeType

from app.utils.utils import send_push_notification
from app.utils.logger_config import setup_logger
from app.config.config import settings

logger = setup_logger()

class NotificationQueue:
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
                "notification_operations",
                ExchangeType.DIRECT,
                durable=True
            )
            
            # Declare queue
            self._queue = await self._channel.declare_queue(
                "push_notifications",
                durable=True,
                arguments={
                    'x-message-ttl': 1000 * 60 * 60 * 24,  # 24 hours
                    'x-dead-letter-exchange': 'notification_dlx',
                    'x-dead-letter-routing-key': 'failed_notifications'
                }
            )
            
            # Declare DLQ for failed notifications
            self._dlq = await self._channel.declare_queue(
                "failed_notifications",
                durable=True
            )
            
            await self._queue.bind(self._exchange, routing_key="push_notification")

    async def process_notification(self, message: IncomingMessage):
        """Process notification message"""
        try:
            data = json.loads(message.body.decode())
            
            tokens = data.get('tokens', [])
            title = data.get('title')
            message_content = data.get('message')
            
            if not tokens or not title or not message_content:
                raise ValueError("Missing required notification fields")
            
            # Attempt to send push notification
            await send_push_notification(
                tokens=tokens,
                title=title,
                message=message_content,
                data=data.get('extra_data')
            )
            
            # If we got here, notification was sent successfully
            await message.ack()
            logger.info(f"Successfully sent notification to {len(tokens)} devices")
            
        except Exception as e:
            logger.error(f"Error processing notification: {str(e)}")
            # If this is already a retry, send to DLQ
            if message.header.delivery_count > 3:
                await message.reject(requeue=False)
            else:
                await message.reject(requeue=True)

    async def publish_notification(self, tokens: List[str], title: str, message: str, extra_data: Optional[Dict[str, Any]] = None):
        """Publish notification message"""
        try:
            await self.connect()
            
            notification_data = {
                "tokens": tokens,
                "title": title,
                "message": message,
                "extra_data": extra_data,
                "timestamp": datetime.now().isoformat()
            }
            
            message = Message(
                json.dumps(notification_data).encode(),
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type='application/json'
            )
            
            await self._exchange.publish(
                message,
                routing_key="push_notification"
            )
            logger.info(f"Published notification for {len(tokens)} devices")
            
        except Exception as e:
            logger.error(f"Failed to publish notification: {str(e)}")
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
                        await self.process_notification(message)
            except Exception as e:
                logger.error(f"Consumer error: {str(e)}")
                self._consuming = False
        
        self._consumer_task = asyncio.create_task(_consume())
        logger.info("Started notification consumer")

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
notification_queue = NotificationQueue()

# Start consumer when application starts
async def start_notification_consumer():
    await notification_queue.start_consuming()

# Stop consumer when application stops
async def stop_notification_consumer():
    await notification_queue.stop_consuming()
