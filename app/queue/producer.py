import asyncio
import json
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from aio_pika import connect_robust, Message, DeliveryMode, ExchangeType
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, insert

from app.models.models import Delivery, Order, Wallet
from app.database.database import get_db
from app.utils.logger_config import setup_logger
from app.config.config import settings, redis_client
from app.schemas.status_schema import (
    OrderStatus,
    DeliveryStatus,
    PaymentStatus,
    TransactionType,
    TransactionDirection,
)

logger = setup_logger()


class CentralQueueProducer:
    def __init__(self):
        self._connection = None
        self._channel = None
        self._exchange = None

    async def connect(self):
        """Establish connection to RabbitMQ"""
        if not self._connection:
            self._connection = await connect_robust(settings.RABBITMQ_URL)
            self._channel = await self._connection.channel()
            self._exchange = await self._channel.declare_exchange(
                "central_operations", ExchangeType.DIRECT, durable=True
            )

    async def publish_message(
        self,
        service: str,
        operation: str,
        payload: Dict[str, Any],
        routing_key: Optional[str] = None,
    ):
        """Publish a message to the specified service and operation"""
        try:
            await self.connect()

            message_data = {
                "service": service,
                "operation": operation,
                "payload": payload,
                "timestamp": datetime.now().isoformat(),
            }

            message = Message(
                json.dumps(message_data).encode(),
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type="application/json",
            )

            await self._exchange.publish(message, routing_key=routing_key or service)
            logger.info(f"Published {service} message for operation {operation}")

        except Exception as e:
            logger.error(f"Failed to publish message: {str(e)}")
            raise

    async def close(self):
        """Close the RabbitMQ connection"""
        if self._connection:
            await self._connection.close()
            self._connection = None
            self._channel = None
            self._exchange = None


# Global producer instance
producer = CentralQueueProducer()
