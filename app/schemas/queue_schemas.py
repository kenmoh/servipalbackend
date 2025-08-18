from enum import Enum
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel
from datetime import datetime
from app.schemas.status_schema import TransactionDirection, TransactionType

class WalletOperationType(str, Enum):
    UPDATE_BALANCE = "update_balance"
    CREATE_TRANSACTION = "create_transaction"

class WalletUpdateMessage(BaseModel):
    operation: WalletOperationType
    wallet_id: UUID
    balance_change: Decimal = Decimal("0")
    escrow_change: Decimal = Decimal("0")
    transaction_type: TransactionType
    transaction_direction: TransactionDirection
    from_user: str | None = None
    to_user: str | None = None
    order_id: UUID | None = None
    created_at: datetime = datetime.now()
    metadata: dict | None = None
