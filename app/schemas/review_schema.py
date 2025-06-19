from datetime import datetime
from uuid import UUID
from enum import Enum
from pydantic import BaseModel, Field



class ReviewerType(str, Enum):
    ORDER = 'order'
    PRODUCT = 'product'


class ReviewCreate(BaseModel):
    order_id: UUID | None = None
    item_id: UUID | None = None
    item_id: UUID | None = None
    reviewee_id: UUID
    rating: int = Field(..., ge=1, le=5)
    comment: str
    review_type: ReviewerType

class ReviewResponse(BaseModel):
    id: UUID
    reviewer_id: UUID
    reviewee_id: UUID
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


class IssueType(str, Enum):
    DAMAGED_ITEMS='damage_items'
    WRONG_ITEMS = "wrong_items"
    LATE_DELIVERY = "late_delivery"
    RIDER_BEHAVIOUR = 'rider_behaviour'
    CUSTOMER_BEHAVIOUR = 'customer_behaviour'
    OTHERS='Others'

class IssueStatus(str, Enum):
    PENDING = "pending"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"

class ReportingType(str, Enum):
    VENDOR = "vendor"
    CUSTOMER = "customer"
    DISPATCH = "dispatch"

class ReportIssueCreate(BaseModel):
    order_id: UUID | None = None
    delivery_id: UUID | None = None
    dispatch_id: UUID | None = None
    vendor_id: UUID | None = None
    reporter_id: UUID | None = None
    customer_id: UUID | None = None
    description: str = Field(..., min_length=10, max_length=1000)
    issue_type: IssueType
    reporting: ReportingType

    class Config:
        from_attributes = True


class ReportResponseSchema(ReportIssueCreate):
    created_at: datetime
    updated_at: datetime


class ReportIssueResponse(BaseModel):
    id: UUID
    order_id: UUID | None 
    delivery_id: UUID | None 
    dispatch_id: UUID | None 
    vendor_id: UUID | None 
    customer_id: UUID | None 
    reporter_id: UUID
    description: str
    issue_type: IssueType
    issue_status: IssueStatus
    reporting: ReportingType
    created_at: datetime
    updated_at: datetime
    
    # Related user information
    # vendor: User | None  = None
    # customer: User | None  = None
    # dispatch: User | None  = None
    # reporter: User | None  = None

class ReportIssueUpdate(BaseModel):
    issue_status: IssueStatus



class ReportListResponse(BaseModel):
    reports: list[ReportIssueResponse]
    total: int
    page: int
    size: int
    has_next: bool
    has_prev: bool