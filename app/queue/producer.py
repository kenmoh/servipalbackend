# app/queue/producer.py
import aio_pika
import json
from typing import Any, Dict, Optional
import logging
from datetime import datetime
from app.queue.connection import rabbitmq_manager
from app.utils.logger_config import setup_logger

logger = setup_logger()

class UniversalProducer:
    def __init__(self):
        self._channel = None
    
    async def publish(
        self,
        exchange_name: str,
        routing_key: str,
        message_data: Dict[str, Any],
        exchange_type: aio_pika.ExchangeType = aio_pika.ExchangeType.TOPIC,
        persistent: bool = True,
        headers: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Universal method to publish messages to any exchange with any routing key
        """
        try:
            if self._channel is None:
                self._channel = await rabbitmq_manager.get_channel()
            
            # Declare exchange (idempotent operation)
            exchange = await self._channel.declare_exchange(
                exchange_name,
                exchange_type,
                durable=True
            )
            
            # Prepare message
            message_body = {
                "data": message_data,
                "metadata": {
                    "published_at": datetime.now().isoformat(),
                    "routing_key": routing_key,
                    "exchange": exchange_name
                }
            }
            
            message = aio_pika.Message(
                body=json.dumps(message_body).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT if persistent else aio_pika.DeliveryMode.NOT_PERSISTENT,
                content_type="application/json",
                headers=headers or {}
            )
            
            # Publish with confirmation
            await exchange.publish(message, routing_key=routing_key)
            
            logger.debug(f"📤 Published to {exchange_name}/{routing_key}: {message_data}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to publish message: {str(e)}")
            # Reconnect on error
            self._channel = None
            return False
    
    async def close(self):
        """Close producer resources"""
        # Channel is managed by connection manager, no need to close separately
        pass

# Global producer instance
producer = UniversalProducer()