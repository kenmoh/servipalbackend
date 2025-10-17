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
    VENDOR_PICKUP_AND_DROPOFF = 'vendor-pickup-and-dropoff'
    # USER_DROPOFF_AND_PICKUP = 'user-dropoff-and-pickup'
    



# class TransactionType(str, Enum):
#     DEBIT = "debit"
#     CREDIT = "credit"


# class UserType(str, Enum):
#     CUSTOMER: str = 'customer'
#     RESTAURANT_VENDOR: str = 'restaurant_vendor'
#     LAUNDRY_VENDOR: str = 'laundry_vendor'


class UserType(str, Enum):
    CUSTOMER: str = os.getenv("CUSTOMER")
    DISPATCH: str = os.getenv("DISPATCH")
    RESTAURANT_VENDOR: str = os.getenv("RESTAURANT_VENDOR")
    LAUNDRY_VENDOR: str = os.getenv("LAUNDRY_VENDOR")
    RIDER: str = os.getenv("RIDER")
    ADMIN: str = os.getenv("ADMIN")
    SUPER_ADMIN = os.getenv("SUPER_ADMIN")
    MODERATOR = os.getenv("MODERATOR")


class DeliveryStatus(str, Enum):
    ACCEPTED: str = "accepted"  # Rider/Dispatch ops
    PENDING: str = "pending"  # Default
    DELIVERED: str = "delivered"  # Rider/Dispatch ops
    RECEIVED: str = "received"  # Sender ops
    CANCELLED: str = "canceled"
    # VENDOR_RECEIVED_LAUNDRY_ITEM: str = "laundry_received"  # Vendor ops


class OrderStatus(str, Enum):
    ACCEPTED: str = "accepted"  # Rider/Dispatch ops
    PENDING: str = "pending"
    DELIVERED: str = "delivered"
    RECEIVED: str = "received"
    CANCELLED: str = "canceled"
    REJECTED: str = "rejected"
    RECEIVED_REJECTED_PRODUCT: str = "received_rejected_product"
    VENDOR_RECEIVED_LAUNDRY_ITEM: str = "laundry_received"  # Vendor ops
    VENDOR_RETURNED_LAUNDRY_ITEM: str = "laundry_returned"  # Vendor ops


class ProdductOrderStatusResponse(BaseModel):
    order_status: OrderStatus


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
    ESCROWED: str = "escrowed"


class AccountStatus(str, Enum):
    PENDING: str = "pending"
    CONFIRMED: str = "confirmed"


class TransactionType(str, Enum):
    FUND_WALLET: str = "fund-wallet"
    PAY_WITH_WALLET: str = "pay-with-wallet"
    USER_TO_USER = "user-to-user"
    USER_TO_SELF = "self"
    WITHDRAWAL = "withdrawal"
    REFUND = "refund"
    ORDER_CANCELLATION = "order-cancellation"


class TransactionDirection(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class PaymentMethod(str, Enum):
    WALLET: str = "wallet"
    CARD: str = "card"
    BANK_TRANSFER: str = "bank_transfer"
    SYSTEM_REFUND: str = "Refund"
    ESCROW_SETTLEMENT: str = "escrow-settlement"
    FUND_REVERSAL: str = "reversal"



class DisputeStatus(str, Enum):
    OPEN: str = "open"
    CLOSED: str = "closed"


class ChangeUserType(BaseModel):
    user_type: UserType
