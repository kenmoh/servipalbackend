from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_db, get_current_user
from app.models.models import User
from app.schemas.review_schema import (
    ReportCreate,
    ReportMessage,
    ReportIssueResponse,
    ReportIssueUpdate,
    MessageCreate,
    ReportStatus,
    StatusUpdate,
    ReportResponseSchema,
    )
from app.services import review_service

router = APIRouter(prefix="/api/reports", tags=["Reports"])


@router.get("/{user_id}", status_code=status.HTTP_200_OK)
async def get_reports_by_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    
) -> list[ReportMessage]:
    """
    Get all reports for the current user (as complainant or defendant)
    """
    return await review_service.get_user_messages(db=db, user_id=user_id)


@router.get("/{report_id}", status_code=status.HTTP_200_OK)
async def get_report_by_id(
    report_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportIssueResponse:
    """
    Get report details by ID
    """
    return await review_service.get_report_by_id(db=db, current_user=current_user, report_id=report_id)


@router.post("/{order_id}/report", status_code=status.HTTP_201_CREATED)
async def create_report(
    order_id: UUID,
    report_data: ReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReportResponseSchema:
    """
    Create a new report
    """
    return await review_service.create_report(db=db, order_id=order_id, current_user=current_user, report_data=report_data)


@router.post("/{report_id}/message", status_code=status.HTTP_201_CREATED)
async def add_message_to_report(
    report_id: UUID,
    message: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
)->MessageCreate:
    """
    Add a message to a report thread
    """
    return await review_service.add_message_to_report(db=db, report_id=report_id, current_user=current_user, message=message)


@router.post("/{report_id}/mark_read", status_code=status.HTTP_200_OK)
async def mark_report_thread_read(
    report_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Mark all messages in a report thread as read for the user
    """
    return await review_service.mark_thread_as_read_for_user(db, report_id, current_user)



@router.put("/{report_id}/status", status_code=status.HTTP_202_ACCEPTED)
async def update_report_status(
    report_id: UUID,
    update_data: ReportIssueUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
)->StatusUpdate:
    """
    Update the status of a report (admin or complainant only)
    """
    return await review_service.update_report_status(db=db, report_id=report_id, new_status=update_data.issue_status, current_user=current_user)


@router.delete("/{report_id}", status_code=status.HTTP_200_OK)
async def delete_report(
    report_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a report and its thread if status is dismissed or resolved
    """
    return await review_service.delete_report_if_allowed(db=db, report_id=report_id, current_user=current_user) 


@router.get("/unread-badge-count", status_code=status.HTTP_200_OK)
async def get_unread_badge_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get unread badge count for current user (report messages)
    """
    return await review_service.get_unread_badge_count(db=db, current_user=current_user)
    