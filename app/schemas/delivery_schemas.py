from decimal import Decimal
from typing import Tuple
from uuid import UUID
from enum import Enum
from datetime import datetime
from pydantic import BaseModel

from app.schemas.order_schema import OrderResponseSchema


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    IN_TRANSIT = "in transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RECEIVED = "received"
    LAUNDRY_DELIVERES_TO_VENDOR = "delivered-to-vendor"


class DeliveryType(str, Enum):
    FOOD = "food"
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
    duration: str
    origin: str
    destination: str
    pickup_coordinates: list[float]
    dropoff_coordinates: list[float]
    delivery_fee: Decimal
    amount_due_dispatch: Decimal
    created_at: datetime


class DeliveryResponse(BaseModel):
    delivery: DeliverySchema | None = None
    order: OrderResponseSchema
