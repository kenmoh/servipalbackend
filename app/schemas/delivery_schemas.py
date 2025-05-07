from decimal import Decimal
from typing import Tuple
from uuid import UUID
from enum import Enum
from datetime import datetime
from pydantic import BaseModel

from app.schemas.order_schema import OrderResponseSchema


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    IN_TRANSIT = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RECEIVED = "received"
    LAUNDRY_DELIVERES_TO_VENDORR = "delivered-to-vendor"


class DeliveryType(str, Enum):
    MEAL = "meal"
    LAUNDRY = "laundry"
    PACKAGE = "package"


class Coordinate(BaseModel):
    latitude: float
    longitude: float


class DeliveryCreate(BaseModel):
    pickup_coordinates: Tuple[float, float]
    dropoff_coordinates: Tuple[float, float]
    distance: float


class DeliverySchema(BaseModel):
    id: UUID
    delivery_type: DeliveryType
    delivery_status: DeliveryStatus
    sender_id: UUID
    vendor_id: UUID | None = None
    dispatch_id: UUID | None = None
    rider_id: UUID | None = None
    distance: Decimal
    #duration: Decimal
    delivery_fee: Decimal
    amount_due_dispatch: Decimal
    created_at: datetime


class DeliveryResponse(BaseModel):

    delivery: DeliverySchema | None = None
    order: OrderResponseSchema


"""
{
  "pickup_coordinates": [
    null,
    null
  ],
  "dropoff_coordinates": [
    null,
    null
  ],
  "distance": "string",
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "delivery_type": "meal",
  "delivery_status": "pending",
  "sender_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "vendor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "dispatch_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "name": "string",
  "image_url": "string",
  "sender_phone_number": "string",
  "vendor_phone_number": "string",
  "rider_name": "string",
  "rider_phone_number": "string",
  "sender_name": "string",
  "duration": "string",
  "delivery_fee": "string",
  "amount_due_dispatch": "string",
  "order": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "vendor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "order_type": "string",
    "total_price": "string",
    "order_payment_status": "string",
    "order_status": "string",
    "amount_due_vendor": "string",
    "payment_link": "string",
    "order_items": [
      {
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "name": "string",
        "price": "string",
        "image_url": "string",
        "description": "string",
        "quantity": 0
      }
    ]
  },
  "creared_at": "2025-05-05T10:42:05.950Z"
}
"""
