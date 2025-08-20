# app/queue/connection.py
import aio_pika
from aio_pika.abc import AbstractRobustConnection, AbstractRobustChannel
from typing import Optional
import asyncio
import logging
from app.config.config import settings
from app.utils.logger_config import setup_logger

logger = setup_logger()

class RabbitMQConnectionManager:
    _instance: Optional['RabbitMQConnectionManager'] = None
    _connection: Optional[AbstractRobustConnection] = None
    _channel: Optional[AbstractRobustChannel] = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_connection(self) -> AbstractRobustConnection:
        if self._connection is None or self._connection.is_closed:
            async with self._lock:
                if self._connection is None or self._connection.is_closed:
                    self._connection = await aio_pika.connect_robust(
                        settings.RABBITMQ_URL,
                        timeout=10,
                        client_properties={"connection_name": "main_app"}
                    )
                    logger.info("✅ RabbitMQ connection established")
        return self._connection
    
    async def get_channel(self) -> AbstractRobustChannel:
        if self._channel is None or self._channel.is_closed:
            async with self._lock:
                if self._channel is None or self._channel.is_closed:
                    connection = await self.get_connection()
                    self._channel = await connection.channel()
                    await self._channel.set_qos(prefetch_count=10)
                    logger.info("✅ RabbitMQ channel created")
        return self._channel
    
    async def close(self):
        if self._channel:
            await self._channel.close()
        if self._connection:
            await self._connection.close()
        logger.info("🔒 RabbitMQ connections closed")

# Global instance
rabbitmq_manager = RabbitMQConnectionManager()