from typing import Optional
import asyncio
from aio_pika import connect_robust, Message, DeliveryMode, IncomingMessage, ExchangeType
import json
from decimal import Decimal
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.models import Transaction, Wallet
from app.database.database import get_db
from app.utils.logger_config import setup_logger
from app.config.config import settings
from app.schemas.status_schema import  PaymentStatus

logger = setup_logger()

class WalletQueue:
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
            # Use a specific exchange name for wallet operations
            self._exchange = await self._channel.declare_exchange(
                "wallet_operations",
                ExchangeType.DIRECT,  
                durable=True
            )
            
            # Declare DLX first
            dlx = await self._channel.declare_exchange(
                'wallet_dlx',
                ExchangeType.DIRECT,
                durable=True
            )
            
            # Declare DLQ
            dlq = await self._channel.declare_queue(
                'failed_wallet_updates',
                durable=True
            )
            await dlq.bind(dlx, routing_key='failed_wallet_updates')
            
            # Declare main queue with consistent arguments
            self._queue = await self._channel.declare_queue(
                "wallet_updates",
                durable=True,
                arguments={
                    'x-message-ttl': 86400000,  # 24 hours in milliseconds
                    # 'x-dead-letter-exchange': 'wallet_dlx',
                    # 'x-dead-letter-routing-key': 'failed_wallet_updates',
                    # 'x-max-retries': 3
                }
            )
            
            # Bind queue to exchange with direct routing key
            await self._queue.bind(
                self._exchange,
                routing_key="wallet_update"  # Use a single direct routing key
            )

    async def _safe_wallet_update(self, db: AsyncSession, wallet_id: UUID, balance_change: Decimal, escrow_change: Decimal) -> None:
        """Perform atomic wallet update"""
        # Get wallet with row lock for update
        # stmt = "SELECT * FROM wallets WHERE id = :wallet_id FOR UPDATE"
        result = await db.execute(select(Wallet).where(Wallet.id==wallet_id))
        # result = await db.execute(stmt, {"wallet_id": wallet_id})
        wallet = result.scalar_one_or_none()
        
        if not wallet:
            raise ValueError(f"Wallet {wallet_id} not found")
        
        # Calculate new balances
        new_balance = wallet.balance + balance_change
        new_escrow = wallet.escrow_balance + escrow_change
        
        # Validate balances
        if new_balance < 0:
            raise ValueError(f"Insufficient balance: {wallet.balance} available, {abs(balance_change)} needed")
        if new_escrow < 0:
            raise ValueError(f"Insufficient escrow balance: {wallet.escrow_balance} available, {abs(escrow_change)} needed")
        
        # Update wallet
        wallet.balance = new_balance
        wallet.escrow_balance = new_escrow

    async def _create_transaction(self, db: AsyncSession, **kwargs) -> Transaction:
        """Create transaction record"""
        transaction = Transaction(**kwargs)
        db.add(transaction)
        await db.flush()
        return transaction


    async def _update_transaction(
        self, 
        db: AsyncSession, 
        tx_ref: str,
        wallet_id: UUID,
        **kwargs
    ) -> Transaction:
        """Update existing transaction """
       
        result = await db.execute(
            select(Transaction).where(
                Transaction.tx_ref == tx_ref,
                Transaction.wallet_id == wallet_id
            )
        )
        transaction = result.scalar_one_or_none()

        if transaction:
            # Update existing transaction
            for key, value in kwargs.items():
                if hasattr(transaction, key):
                    setattr(transaction, key, value)
            logger.info(f"Updated existing transaction: {transaction.id}")
            return transaction

    async def process_wallet_message(self, message: IncomingMessage):
        try:
            data = json.loads(message.body.decode())
            
            async for db in get_db():
                try:
                    async with db.begin():
                        # Update wallet with decimal conversion safety
                        await self._safe_wallet_update(
                            db=db,
                            wallet_id=UUID(data['wallet_id']),
                            balance_change=Decimal(str(data.get('balance_change', 0))),
                            escrow_change=Decimal(str(data.get('escrow_change', 0)))
                        )

                        # Determine if this should create new transaction or update existing
                        is_new_transaction = data.get('metadata', {}).get('is_new_transaction', True)

                        if is_new_transaction:
                            # Create new transaction record
                            await self._create_transaction(
                                db=db,
                                wallet_id=UUID(data['wallet_id']),
                                tx_ref=data['tx_ref'],
                                amount=abs(Decimal(str(data.get('balance_change', 0))) or 
                                        Decimal(str(data.get('escrow_change', 0)))),
                                transaction_type=data.get('transaction_type'),
                                transaction_direction=data.get('transaction_direction'),
                                payment_status=data.get('payment_status'),
                                from_user=data.get('from_user'),
                                to_user=data.get('to_user'),
                                metadata=data.get('metadata', {})
                            )
                        else:
                            # Update existing transaction
                            await self._update_transaction(
                                db=db,
                                tx_ref=data['tx_ref'],
                                wallet_id=UUID(data['wallet_id']),
                                to_user=data.get('to_user'),
                                metadata=data.get('metadata', {})
                            )

                    await message.ack()
                    
                except Exception as db_error:
                    logger.error(f"Database error: {str(db_error)}")
                    await message.reject(requeue=True)
                    
        except Exception as e:
            logger.error(f"Message processing error: {str(e)}")
            await message.reject(requeue=False)

    async def publish_wallet_update(self, **kwargs):
        """Publish wallet update message with enhanced routing and headers"""
        try:
            await self.connect()
            
            # Create message with operation type in headers
            headers = {
                "wallet_id": str(kwargs.get('wallet_id')),
                "operation": (
                    "escrow_hold" if kwargs.get('escrow_change', 0) > 0
                    else "escrow_release" if kwargs.get('escrow_change', 0) < 0
                    else "wallet_update"
                ),
                "transaction_type": kwargs.get('transaction_type'),
                "created_at": datetime.now().isoformat(),
                "source": "wallet_service",
            }
            
            if kwargs.get('metadata'):
                headers.update(kwargs['metadata'])
            
            message = Message(
                json.dumps(kwargs, default=str).encode(),
                delivery_mode=DeliveryMode.PERSISTENT,
                content_type='application/json',
                headers=headers
            )
            
            await self._exchange.publish(
                message,
                routing_key="wallet_update"  # Use consistent routing key
            )
            logger.info(f"Published wallet update: wallet_id={kwargs.get('wallet_id')}")
            
        except Exception as e:
            logger.error(f"Failed to publish wallet update: {str(e)}")
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
                        await self.process_wallet_message(message)
            except Exception as e:
                logger.error(f"Consumer error: {str(e)}")
                self._consuming = False
        
        self._consumer_task = asyncio.create_task(_consume())
        logger.info("Started wallet operation consumer")

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
wallet_service = WalletQueue()

# Start consumer when application starts
async def start_wallet_consumer():
    await wallet_service.start_consuming()

# Stop consumer when application stops
async def stop_wallet_consumer():
    await wallet_service.stop_consuming()
