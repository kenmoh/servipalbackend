from datetime import datetime
from typing import Union
from uuid import UUID
from enum import Enum
import dateutil
from pydantic import BaseModel, Field


class ReviewType(str, Enum):
    ORDER = "order"
    PRODUCT = "product"


class ReportedUserType(str, Enum):
    VENDOR = "vendor"
    DISPATCH = "dispatch"
    CUSTOMER = "customer"


class MessageType(str, Enum):
    BROADCAST = "broadcast"
    REPORT = "report"

class ReportTag(str, Enum):
    COMPLAINANT = 'complainant'
    DEFENDANT= 'defendanat'

class ReviewCreate(BaseModel):
    order_id: UUID | None = None
    item_id: UUID | None = None
    # delivery_id: UUID
    rating: int = Field(..., ge=1, le=5)
    comment: str
    review_type: ReviewType


class ReviewResponse(BaseModel):
    id: UUID
    complainant_id: UUID
    complainant: UUID
    rating: int
    comment: str
    created_at: datetime


class ReviewerProfile(BaseModel):
    id: UUID
    full_name: str
    profile_image_url: str


class VendorReviewResponse(BaseModel):
    id: UUID
    rating: int
    comment: str
    created_at: datetime
    reviewer: ReviewerProfile


class ReportType(str, Enum):
    DAMAGED_ITEMS = "damage_items"
    WRONG_ITEMS = "wrong_items"
    LATE_DELIVERY = "late_delivery"
    RIDER_BEHAVIOUR = "rider_behaviour"
    CUSTOMER_BEHAVIOUR = "customer_behaviour"
    OTHERS = "Others"


class ReportStatus(str, Enum):
    PENDING = "pending"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"



class ReportCreate(BaseModel):
    order_id: UUID | None = None
    description: str = Field(..., min_length=10, max_length=500)
    report_type: ReportType
    reported_user_type: ReportedUserType

    class Config:
        from_attributes = True


class ReportResponseSchema(ReportCreate):
    created_at: datetime
    updated_at: datetime
    report_status: ReportStatus
    report_tag: ReportTag


class ReportIssueResponse(BaseModel):
    id: UUID
    order_id: UUID | None
    delivery_id: UUID | None
    dispatch_id: UUID | None
    vendor_id: UUID | None
    customer_id: UUID | None
    reporter_id: UUID
    description: str
    issue_status: ReportStatus
    report_type: ReportType
    created_at: datetime
    updated_at: datetime


class ReportIssueUpdate(BaseModel):
    issue_status: ReportStatus


class ReportListResponse(BaseModel):
    reports: list[ReportIssueResponse]
    total: int
    page: int
    size: int
    has_next: bool
    has_prev: bool


class MessageCreate(BaseModel):
    content: str



class SenderInfo(BaseModel):
    name: str
    avatar: str| None = None


# Message in a thread (for report messages)
class ThreadMessage(BaseModel):
    sender: SenderInfo
    message_type: MessageType
    role: str 
    date: datetime
    content: str
    read: bool


# Broadcast/individual notification message
class BroadcastMessage(BaseModel):
    id: str
    message_type:MessageType
    read: bool
    sender: SenderInfo
    date: str 
    title: str
    content: str


# Report/threaded notification message
class ReportMessage(BaseModel):
    id: UUID
    complainant_id: UUID
    report_type: ReportedUserType
    report_tag: ReportTag
    report_status: ReportStatus
    description: str
    created_at: datetime
    is_read: bool
    thread: list[ThreadMessage]



# Union type for notification messages
MessageResponse = Union[BroadcastMessage, ReportMessage]


# List return schema
class MessageListResponse(BaseModel):
    messages: list[MessageResponse]

class StatusUpdate(BaseModel):
    report_status: ReportStatus

class BadgeCount(BaseModel):
    unread_count: int


