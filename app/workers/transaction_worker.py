import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional
from aio_pika import connect_robust, Message, DeliveryMode
from app.models.models import Transaction, TransactionStatus
from app.database.database import get_db
from app.utils.logger_config import setup_logger
from app.config.config import settings

logger = setup_logger()

# Constants for retry handling
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
DLQ_NAME = "wallet_transactions_dlq"

class TransactionError(Exception):
    """Base exception for transaction processing errors"""
    pass

async def setup_queues(channel):
    """Set up the main queue and dead letter queue with proper configurations"""
    # Declare the dead letter queue
    dlq = await channel.declare_queue(
        DLQ_NAME,
        durable=True
    )
    
    # Declare the main queue with dead letter exchange configuration
    queue = await channel.declare_queue(
        "wallet_transactions",
        durable=True,
        arguments={
            'x-dead-letter-exchange': '',  # Default exchange
            'x-dead-letter-routing-key': DLQ_NAME,
            'x-message-ttl': 1000 * 60 * 60 * 24  # 24 hours
        }
    )
    
    return queue, dlq

async def process_transaction(message_body: dict, retry_count: int = 0) -> None:
    """Process a single transaction with retry logic"""
    async for db in get_db():
        try:
            # Extract transaction data and add metadata
            transaction_data = {
                **message_body,
                'status': TransactionStatus.PENDING,
                'retry_count': retry_count,
                'last_retry': datetime.now() if retry_count > 0 else None
            }
            
            # Create and save transaction
            transaction = Transaction(**transaction_data)
            db.add(transaction)
            await db.commit()
            
            logger.info(f"Successfully processed transaction {transaction.id}")
            
        except Exception as e:
            await db.rollback()
            
            if retry_count >= MAX_RETRIES:
                logger.error(f"Max retries reached for transaction. Moving to DLQ. Error: {str(e)}")
                transaction_data['status'] = TransactionStatus.FAILED
                raise TransactionError(f"Failed to process transaction after {MAX_RETRIES} retries: {str(e)}")
            
            # Calculate exponential backoff
            delay = RETRY_DELAY * (2 ** retry_count)
            logger.warning(f"Transaction failed, attempting retry {retry_count + 1} in {delay} seconds. Error: {str(e)}")
            
            # Sleep with exponential backoff
            await asyncio.sleep(delay)
            await process_transaction(message_body, retry_count + 1)

async def handle_dlq_message(message: Message, dlq_channel):
    """Handle messages in the dead letter queue"""
    try:
        message_body = json.loads(message.body.decode())
        logger.error(f"DLQ Message received: {message_body}")
        
        # Here you could:
        # 1. Send alerts to admin
        # 2. Store in a separate DB table for manual review
        # 3. Attempt special recovery procedures
        
        # For now, we'll just acknowledge the message
        await message.ack()
        
    except Exception as e:
        logger.error(f"Error handling DLQ message: {str(e)}")
        # Requeue the message if we can't process it
        await message.reject(requeue=True)

async def monitor_dlq():
    """Monitor the dead letter queue for failed transactions"""
    connection = await connect_robust(settings.RABBITMQ_URL)
    async with connection:
        channel = await connection.channel()
        dlq = await channel.declare_queue(DLQ_NAME, durable=True)
        
        async with dlq.iterator() as queue_iter:
            async for message in queue_iter:
                await handle_dlq_message(message, channel)

async def run_worker():
    """Main worker function"""
    # Start DLQ monitor in background
    asyncio.create_task(monitor_dlq())
    
    while True:
        try:
            connection = await connect_robust(settings.RABBITMQ_URL)
            async with connection:
                channel = await connection.channel()
                queue, _ = await setup_queues(channel)
                
                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        try:
                            async with message.process():
                                message_body = json.loads(message.body.decode())
                                await process_transaction(message_body)
                                
                        except TransactionError as e:
                            # Move to DLQ - don't requeue
                            logger.error(f"Transaction Error: {str(e)}")
                            await message.reject(requeue=False)
                            
                        except Exception as e:
                            logger.error(f"Unexpected error processing message: {str(e)}")
                            # Requeue the message for retry
                            await message.reject(requeue=True)
                            
        except Exception as e:
            logger.error(f"Worker connection error: {str(e)}")
            # Wait before reconnecting
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run_worker())