import asyncio
import json
from uuid import UUID
from typing import  Dict, Callable
from aio_pika import (
    connect_robust,

    ExchangeType,
    IncomingMessage,
)


from app.utils.logger_config import setup_logger
from app.config.config import settings



logger = setup_logger()


class BaseQueueConsumer:
    def __init__(self, service_name: str, queue_name: str):
        self.service_name = service_name
        self.queue_name = queue_name
        self._connection = None
        self._channel = None
        self._exchange = None
        self._queue = None
        self._consuming = False
        self._consumer_task = None
        self._operation_handlers: Dict[str, Callable] = {}

    async def connect(self):
        """Establish connection to RabbitMQ"""
        if not self._connection:
            self._connection = await connect_robust(settings.RABBITMQ_URL)
            self._channel = await self._connection.channel()
            self._exchange = await self._channel.declare_exchange(
                "central_operations", ExchangeType.DIRECT, durable=True
            )
            self._queue = await self._channel.declare_queue(
                self.queue_name,
                durable=True,
                arguments={
                    "x-message-ttl": 1000 * 60 * 60 * 24,  # 24 hours
                    "x-dead-letter-exchange": f"{self.service_name}_dlx",
                    "x-dead-letter-routing-key": f"failed_{self.service_name}_updates",
                },
            )
            await self._queue.bind(self._exchange, routing_key=self.service_name)
            await self._channel.declare_queue(
                f"failed_{self.service_name}_updates", durable=True
            )

    async def process_message(self, message: IncomingMessage):
        """Process incoming message by dispatching to the appropriate handler"""
        try:
            data = json.loads(message.body.decode())
            operation = data.get("operation")
            payload = data.get("payload", {})

            if not operation:
                raise ValueError("Missing operation field in message")

            handler = self._operation_handlers.get(operation)
            if not handler:
                raise ValueError(
                    f"No handler for operation {operation} in {self.service_name}"
                )

            await handler(payload)
            await message.ack()
            logger.info(
                f"Processed {self.service_name} message for operation {operation}"
            )

        except Exception as e:
            logger.error(f"Error processing {self.service_name} message: {str(e)}")
            if message.headers.get("delivery_count", 0) > 3:
                await message.reject(requeue=False)
            else:
                await message.reject(requeue=True)

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
                        await self.process_message(message)
            except Exception as e:
                logger.error(f"Consumer error in {self.service_name}: {str(e)}")
                self._consuming = False

        self._consumer_task = asyncio.create_task(_consume())
        logger.info(f"Started {self.service_name} consumer")

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
            self._queue = None
