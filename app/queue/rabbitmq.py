import aio_pika
from aio_pika import Message, DeliveryMode, ExchangeType
import json
from typing import Any
from app.config.config import settings
from app.schemas.queue_schemas import WalletUpdateMessage
from app.utils.logger_config import setup_logger

logger = setup_logger()

class RabbitMQService:
    """Simplified RabbitMQ service for cloud instance publishing only"""
    
    async def publish_wallet_operation(self, message: WalletUpdateMessage):
        """
        Publish a wallet operation message to cloud RabbitMQ instance.
        Uses connection per publish to avoid connection management complexity.
        """
        try:
            # Create new connection for each publish
            connection = await aio_pika.connect_robust(
                settings.RABBITMQ_URL,
                client_properties={
                    'connection_name': 'quickpick_wallet_publisher'
                }
            )
            
            async with connection:
                # Create channel
                channel = await connection.channel()
                
                # Get exchange
                exchange = await channel.declare_exchange(
                    "wallet_operations",
                    ExchangeType.TOPIC,
                    durable=True,
                    auto_delete=False
                )
                
                # Create message with all necessary metadata
                routing_key = f"wallet.{message.operation.value}"
                message_body = message.json()
                
                # Publish with persistent delivery and metadata
                await exchange.publish(
                    Message(
                        message_body.encode(),
                        delivery_mode=DeliveryMode.PERSISTENT,
                        content_type='application/json',
                        headers={
                            'operation': message.operation.value,
                            'wallet_id': str(message.wallet_id),
                            'created_at': message.created_at.isoformat(),
                            'service': 'servipal_wallet'
                        }
                    ),
                    routing_key=routing_key
                )
                
        except Exception as e:
            logger.error(f"Failed to publish wallet operation: {str(e)}")
            # You might want to implement a retry mechanism or raise the error
            raise

# Global instance
rabbitmq_service = RabbitMQService()