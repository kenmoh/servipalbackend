from datetime import datetime
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum


class NotificationType(str, Enum):
    BROADCAST = "broadcast"
    INDIVIDUAL = "individual"
    REPORT_THREAD = "report_thread"


class SenderRole(str, Enum):
    REPORTER = "reporter"
    REPORTEE = "reportee"
    ADMIN = "admin"


class NotificationMessageSchema(BaseModel):
    id: UUID
    sender_id: UUID
    sender_role: SenderRole
    content: str
    is_read: bool
    created_at: datetime
    updated_at: datetime
    sender: Optional[dict] = None  # Will contain sender profile info

    class Config:
        from_attributes = True


class NotificationRecipientSchema(BaseModel):
    id: UUID
    recipient_id: UUID
    is_read: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class NotificationSchema(BaseModel):
    id: UUID
    notification_type: NotificationType
    recipient_id: Optional[UUID] = None
    report_issue_id: Optional[UUID] = None
    sender_id: UUID
    title: str
    content: str
    is_read: bool
    is_broadcast: bool
    created_at: datetime
    updated_at: datetime
    thread_messages: List[NotificationMessageSchema] = []
    recipients: List[NotificationRecipientSchema] = []

    class Config:
        from_attributes = True


class NotificationCreateSchema(BaseModel):
    notification_type: NotificationType
    recipient_id: Optional[UUID] = None
    report_issue_id: Optional[UUID] = None
    title: str
    content: str
    is_broadcast: bool = False


class NotificationMessageCreateSchema(BaseModel):
    content: str
    sender_role: Optional[SenderRole] = None  # Will be auto-determined if not provided


class BroadcastNotificationCreateSchema(BaseModel):
    title: str
    content: str
    recipient_ids: List[UUID] = Field(
        ..., description="List of user IDs to send broadcast to"
    )


class IndividualNotificationCreateSchema(BaseModel):
    recipient_id: UUID
    title: str
    content: str


class ReportThreadNotificationCreateSchema(BaseModel):
    report_issue_id: UUID
    title: str
    content: str


class NotificationResponseSchema(BaseModel):
    id: UUID
    notification_type: NotificationType
    title: str
    content: str
    is_read: bool
    is_broadcast: bool
    created_at: datetime
    sender: dict = Field(..., description="Sender information")
    thread_messages: List[dict] = []  # Changed to dict to include sender info
    recipients: List[NotificationRecipientSchema] = []

    class Config:
        from_attributes = True


class NotificationListResponseSchema(BaseModel):
    notifications: List[NotificationResponseSchema]
    total_count: int
    unread_count: int


class MarkNotificationReadSchema(BaseModel):
    notification_id: UUID


class MarkAllNotificationsReadSchema(BaseModel):
    notification_ids: List[UUID]


class NotificationStatsSchema(BaseModel):
    total_notifications: int
    unread_notifications: int
    broadcast_notifications: int
    individual_notifications: int
    report_thread_notifications: int
