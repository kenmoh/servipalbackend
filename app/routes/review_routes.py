from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_db, get_current_user
from app.models.models import User
from app.schemas.review_schema import (
    ReviewCreate,
    ReviewResponse,
    VendorReviewResponse,
    ReviewerType,
    ReportIssueResponse,
    ReportIssueUpdate,
    ReportIssueCreate
)
from app.services import review_service

router = APIRouter(prefix="/api/reviews", tags=["Reviews"])
report = APIRouter(prefix="/api/reports", tags=["Reports"])

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
)
async def create_new_review(
    data: ReviewCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReviewResponse:
    """
    Endpoint to create a new review
    """
    return await review_service.create_review(db=db, current_user=current_user, data=data)


@report.post(
    "",
    status_code=status.HTTP_201_CREATED,
)
async def create_report(
    report_data: ReportIssueCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportIssueResponse:
    """
    Endpoint to create a new review
    """
    return await review_service.create_report(db=db, current_user=current_user, report_data=report_data)


@router.get(
    "/{vendor_id}/vendor-reviews",
    status_code=status.HTTP_200_OK,
)
async def get_user_reviews(
    vendor_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[VendorReviewResponse]:
    """
    Endpoint to get current user reviews
    """
    return await review_service.fetch_vendor_reviews(db=db, vendor_id:vendor_id, current_user=current_user)


@report.get(
    "",
    status_code=status.HTTP_200_OK,
)
async def get_reports_by_user(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ReportIssueResponse]:
    """
    Endpoint to get current user reviews
    """
    return await review_service.get_reports_by_user(db=db, current_user=current_user)




@report.get(
    "/{report_id}/detail",
    status_code=status.HTTP_200_OK,
)
async def get_report_by_id(
    report_id: UUID,
    background_task: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportIssueResponse:
    """
   Get report details
    """
    return await review_service.get_report_by_id(db=db, current_user=current_user, report_id=report_id)


@report.put(
    "/{report_id}/update",
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_report_status(
    report_id: UUID,
    update_data: ReportIssueUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportIssueUpdate:
    """
    Update report by user
    """
    return await review_service.update_report_status(db=db, update_data=update_data, report_id=report_id, current_user=current_user)