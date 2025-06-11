import os
from enum import Enum
from pydantic import BaseModel

from dotenv import load_dotenv

load_dotenv()


class BankSchema(BaseModel):
    id: int
    code: str
    name: str


class RequireDeliverySchema(str, Enum):
    PICKUP = "pickup"
    DELIVERY = "delivery"


class TransactionType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class UserType(str, Enum):
    CUSTOMER: str = os.getenv("CUSTOMER")
    DISPATCH: str = os.getenv("DISPATCH")
    VENDOR: str = os.getenv("VENDOR")
    RIDER: str = os.getenv("RIDER")
    ADMIN: str = os.getenv("ADMIN")


class DeliveryStatus(str, Enum):
    ACCEPTED: str = "accepted"  # Rider/Dispatch ops
    PENDING: str = "pending"  # Default
    DELIVERED: str = "delivered"  # Rider/Dispatch ops
    RECEIVED: str = "received"  # Sender ops
    VENDOR_RECEIVED_LAUNDRY_ITEM: str = "laundry_received"  # Vendor ops
    CANCELLED: str = "canceled"  # Vendor/Rider/Dispath ops


class OrderStatus(str, Enum):
    PENDING: str = "pending"
    DELIVERED: str = "delivered"
    RECEIVED: str = "received"
    CANCELLED: str = "canceled"
    REJECTED: str = "rejected"


class ItemStatus(str, Enum):
    WASHING: str = "washing"
    WASHED: str = "washed"
    COOKING: str = "cooking"
    SERVED: str = "served"


class ItemStatusResponse(BaseModel):
    item_status: ItemStatus


class TransactionStatus(str, Enum):
    PENDING = "pending"
    RECEIVED = "received"
    RECEIVED_REJECTED_ITEM = "received-rejected-item"
    DELIVERED = "delivered"
    REJECTED = "rejected"
    CANCELED = "canceled"


class OrderType(str, Enum):
    PACKAGE: str = "package"
    FOOD: str = "food"
    LAUNDRY: str = "laundry"
    PRODUCT: str = "product"


class PaymentStatus(str, Enum):
    FAILED: str = "failed"
    PAID: str = "paid"
    CANCELLED: str = "cancelled"
    PENDING: str = "pending"
    COMPLETED: str = "completed"
    SUCCESSFUL: str = "successful"


class AccountStatus(str, Enum):
    PENDING: str = "pending"
    CONFIRMED: str = "confirmed"


# class TransactionType(str, Enum):
#     CREDIT: str = "credit"
#     DEBIT: str = "debit"
#     FUND_WALLET: str = "fund wallet"
#     PAY_WITH_WALLET: str = "paid with wallet"


class DisputeStatus(str, Enum):
    OPEN: str = "open"
    CLOSED: str = "closed"


class ChangeUserType(BaseModel):
    user_type: UserType
