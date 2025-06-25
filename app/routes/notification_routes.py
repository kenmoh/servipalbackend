from typing import List
from uuid import UUID
import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_db
from app.auth.auth import get_current_user
from app.models.models import User
from app.schemas.notification_schemas import (
    BroadcastNotificationCreateSchema,
    IndividualNotificationCreateSchema,
    ReportThreadNotificationCreateSchema,
    NotificationMessageCreateSchema,
    NotificationResponseSchema,
    NotificationStatsSchema,
    NotificationMessageSchema,
)
from app.services.notification_service import (
    create_broadcast_notification,
    create_individual_notification,
    create_report_thread_notification,
    add_message_to_thread,
    get_user_notifications,
    mark_notification_read,
    mark_notification_read_on_view,
    mark_all_notifications_read,
    mark_thread_messages_read,
    get_notification_stats,
    get_unread_badge_count,
    get_new_notifications,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.post("/broadcast", response_model=NotificationResponseSchema)
async def create_broadcast(
    broadcast_data: BroadcastNotificationCreateSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a broadcast notification (Admin only)"""
    # TODO: Add admin role check
    # if current_user.user_type != "ADMIN":
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Only admins can create broadcast notifications"
    #     )

    return await create_broadcast_notification(
        db=db,
        sender=current_user,
        broadcast_data=broadcast_data,
    )


@router.post("/individual", response_model=NotificationResponseSchema)
async def create_individual(
    notification_data: IndividualNotificationCreateSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create an individual notification (Admin only)"""
    # TODO: Add admin role check
    # if current_user.user_type != "ADMIN":
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Only admins can create individual notifications"
    #     )

    return await create_individual_notification(
        db=db,
        sender=current_user,
        notification_data=notification_data,
    )


@router.post("/report-thread", response_model=NotificationResponseSchema)
async def create_report_thread(
    notification_data: ReportThreadNotificationCreateSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a notification for a report thread (Admin only)"""
    # TODO: Add admin role check
    # if current_user.user_type != "ADMIN":
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Only admins can create report thread notifications"
    #     )

    return await create_report_thread_notification(
        db=db,
        sender=current_user,
        notification_data=notification_data,
    )


@router.post("/{notification_id}/messages", response_model=NotificationMessageSchema)
async def add_message(
    notification_id: UUID,
    message_data: NotificationMessageCreateSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a message to a notification thread (sender_role will be auto-determined if not provided)"""
    return await add_message_to_thread(
        db=db,
        notification_id=notification_id,
        sender=current_user,
        message_data=message_data,
    )


@router.get("/")
async def get_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    mark_read: bool = Query(
        False, description="Mark notifications as read when fetched"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get user notifications with optional mark as read"""
    return await get_user_notifications(
        db=db,
        user=current_user,
        skip=skip,
        limit=limit,
        mark_read=mark_read,
    )


@router.get("/stats", response_model=NotificationStatsSchema)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get notification statistics for the current user"""
    return await get_notification_stats(
        db=db,
        user=current_user,
    )


@router.put("/{notification_id}/read")
async def mark_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a notification as read"""
    success = await mark_notification_read(
        db=db,
        notification_id=notification_id,
        user=current_user,
    )

    if success:
        return {"message": "Notification marked as read"}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found or already read",
        )


@router.put("/{notification_id}/view")
async def mark_read_on_view(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a notification as read when user views it (for automatic marking)"""
    success = await mark_notification_read_on_view(
        db=db,
        notification_id=notification_id,
        user=current_user,
    )

    if success:
        return {"message": "Notification marked as read on view"}
    else:
        return {"message": "Notification not found or access denied"}


@router.put("/{notification_id}/thread/read")
async def mark_thread_read(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark all messages in a notification thread as read"""
    success = await mark_thread_messages_read(
        db=db,
        notification_id=notification_id,
        user=current_user,
    )

    if success:
        return {"message": "Thread messages marked as read"}
    else:
        return {"message": "Thread not found or access denied"}


@router.put("/read-all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark all notifications as read for the current user"""
    success = await mark_all_notifications_read(
        db=db,
        user=current_user,
    )

    if success:
        return {"message": "All notifications marked as read"}
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to mark notifications as read",
        )


@router.get("/{notification_id}", response_model=NotificationResponseSchema)
async def get_notification(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific notification by ID"""
    # This would need to be implemented in the service
    # For now, we'll return a placeholder
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Get specific notification endpoint not implemented yet",
    )


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a notification (Admin only)"""
    # TODO: Add admin role check and implement deletion
    # if current_user.user_type != "ADMIN":
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Only admins can delete notifications"
    #     )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Delete notification endpoint not implemented yet",
    )


@router.get("/badge-count")
async def get_badge_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the count of unread notifications for the current user"""
    return await get_unread_badge_count(
        db=db,
        user=current_user,
    )


@router.get("/stream")
async def stream_notifications_sse(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Server-Sent Events (SSE) endpoint for real-time notifications

    Frontend Usage:
    ```javascript
    const eventSource = new EventSource('/api/notifications/stream');

    eventSource.addEventListener('notification', function(event) {
        const notifications = JSON.parse(event.data);
        // Handle new notifications
        showNotifications(notifications);
    });

    eventSource.addEventListener('unread_count', function(event) {
        const data = JSON.parse(event.data);
        // Update badge count
        updateBadge(data.unread_count);
    });

    eventSource.addEventListener('error', function(event) {
        console.error('SSE Error:', event.data);
    });
    ```
    """

    async def event_generator():
        try:
            while True:
                # Check if client is still connected
                if await request.is_disconnected():
                    break

                # Get new notifications
                new_notifications = await get_new_notifications(db, current_user)

                # Get unread count
                badge_data = await get_unread_badge_count(db, current_user)

                # Send notification events
                if new_notifications:
                    yield f"event: notification\ndata: {json.dumps(new_notifications)}\n\n"

                # Send badge count updates
                yield f"event: unread_count\ndata: {json.dumps(badge_data)}\n\n"

                await asyncio.sleep(5)  # Check every 5 seconds

        except Exception as e:
            # Send error event
            yield f"event: error\ndata: {json.dumps({'message': 'Stream error occurred'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Cache-Control",
        },
    )


# Frontend Integration Examples:
#
# 1. When user opens notifications page:
# GET /notifications?mark_read=true
#
# 2. When user taps on a specific notification:
# PUT /notifications/{notification_id}/view
#
# 3. When user manually marks as read:
# PUT /notifications/{notification_id}/read
#
# 4. Mark all as read:
# PUT /notifications/read-all
#
# 5. Get unread badge count:
# GET /notifications/badge-count
#
# 6. Real-time streaming:
# GET /notifications/stream
