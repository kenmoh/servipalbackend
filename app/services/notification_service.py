from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, insert, func, and_, or_
from sqlalchemy.orm import selectinload, joinedload
import json
import asyncio

from app.models.models import (
    Notification,
    NotificationMessage,
    NotificationRecipient,
    User,
    ReportIssue,
)
from app.schemas.notification_schemas import (
    NotificationCreateSchema,
    NotificationMessageCreateSchema,
    BroadcastNotificationCreateSchema,
    IndividualNotificationCreateSchema,
    NotificationMessageSchema,
    NotificationRecipientSchema,
    ReportThreadNotificationCreateSchema,
    NotificationResponseSchema,
    NotificationListResponseSchema,
    NotificationStatsSchema,
    NotificationType,
    SenderRole,
)
from app.utils.utils import send_push_notification, get_user_notification_token


async def create_broadcast_notification(
    db: AsyncSession,
    sender: User,
    broadcast_data: BroadcastNotificationCreateSchema,
) -> NotificationResponseSchema:
    """Create a broadcast notification for multiple users"""
    try:
        # Create the main notification
        notification = Notification(
            notification_type=NotificationType.BROADCAST,
            sender_id=sender.id,
            title=broadcast_data.title,
            content=broadcast_data.content,
            is_broadcast=True,
        )

        db.add(notification)
        await db.flush()  # Get the ID without committing

        # Create recipient records for each user
        recipient_records = []
        notification_tokens = []

        for recipient_id in broadcast_data.recipient_ids:
            # Check if user exists
            user_result = await db.execute(select(User).where(User.id == recipient_id))
            user = user_result.scalar_one_or_none()

            if user:
                recipient_record = NotificationRecipient(
                    notification_id=notification.id,
                    recipient_id=recipient_id,
                )
                recipient_records.append(recipient_record)

                # Collect notification tokens for push notifications
                if user.notification_token:
                    notification_tokens.append(user.notification_token)

        db.add_all(recipient_records)
        await db.commit()
        await db.refresh(notification)

        # Send push notifications
        if notification_tokens:
            await send_push_notification(
                tokens=notification_tokens,
                title=broadcast_data.title,
                message=broadcast_data.content,
                navigate_to="/notifications",
            )

        return await format_notification_response(db, notification)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create broadcast notification: {str(e)}",
        )


async def create_individual_notification(
    db: AsyncSession,
    sender: User,
    notification_data: IndividualNotificationCreateSchema,
) -> NotificationResponseSchema:
    """Create an individual notification for a specific user"""
    try:
        # Check if recipient exists
        recipient_result = await db.execute(
            select(User).where(User.id == notification_data.recipient_id)
        )
        recipient = recipient_result.scalar_one_or_none()

        if not recipient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Recipient user not found"
            )

        # Create the notification
        notification = Notification(
            notification_type=NotificationType.INDIVIDUAL,
            recipient_id=notification_data.recipient_id,
            sender_id=sender.id,
            title=notification_data.title,
            content=notification_data.content,
            is_broadcast=False,
        )

        db.add(notification)
        await db.commit()
        await db.refresh(notification)

        # Send push notification
        if recipient.notification_token:
            await send_push_notification(
                tokens=[recipient.notification_token],
                title=notification_data.title,
                message=notification_data.content,
                navigate_to="/notifications",
            )

        return await format_notification_response(db, notification)

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create individual notification: {str(e)}",
        )


async def create_report_thread_notification(
    db: AsyncSession,
    sender: User,
    notification_data: ReportThreadNotificationCreateSchema,
) -> NotificationResponseSchema:
    """Create a notification for a report thread"""
    try:
        # Check if report issue exists
        report_result = await db.execute(
            select(ReportIssue).where(
                ReportIssue.id == notification_data.report_issue_id
            )
        )
        report_issue = report_result.scalar_one_or_none()

        if not report_issue:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Report issue not found"
            )

        # Create the notification
        notification = Notification(
            notification_type=NotificationType.REPORT_THREAD,
            report_issue_id=notification_data.report_issue_id,
            sender_id=sender.id,
            title=notification_data.title,
            content=notification_data.content,
            is_broadcast=False,
        )

        db.add(notification)
        await db.commit()
        await db.refresh(notification)

        # Send push notifications to relevant users
        notification_tokens = []

        # Add reporter
        if report_issue.reporter.notification_token:
            notification_tokens.append(report_issue.reporter.notification_token)

        # Add vendor if exists
        if report_issue.vendor and report_issue.vendor.notification_token:
            notification_tokens.append(report_issue.vendor.notification_token)

        # Add customer if exists
        if report_issue.customer and report_issue.customer.notification_token:
            notification_tokens.append(report_issue.customer.notification_token)

        # Add dispatch if exists
        if report_issue.dispatch and report_issue.dispatch.notification_token:
            notification_tokens.append(report_issue.dispatch.notification_token)

        if notification_tokens:
            await send_push_notification(
                tokens=notification_tokens,
                title=notification_data.title,
                message=notification_data.content,
                navigate_to="/notifications",
            )

        return await format_notification_response(db, notification)

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create report thread notification: {str(e)}",
        )


async def add_message_to_thread(
    db: AsyncSession,
    notification_id: UUID,
    sender: User,
    message_data: NotificationMessageCreateSchema,
) -> NotificationMessageSchema:
    """Add a message to a notification thread"""
    try:
        # Check if notification exists
        notification_result = await db.execute(
            select(Notification).where(Notification.id == notification_id)
        )
        notification = notification_result.scalar_one_or_none()

        if not notification:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
            )

        # Auto-determine sender role if not provided
        sender_role = message_data.sender_role
        if not sender_role and notification.report_issue:
            sender_role = determine_user_role(sender, notification.report_issue)
        elif not sender_role:
            sender_role = SenderRole.REPORTER  # Default fallback

        # Create the message
        message = NotificationMessage(
            notification_id=notification_id,
            sender_id=sender.id,
            sender_role=sender_role,
            content=message_data.content,
        )

        db.add(message)
        await db.commit()
        await db.refresh(message)

        # Send push notifications to thread participants
        await send_thread_notifications(db, notification, message)

        return NotificationMessageSchema.model_validate(message)

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add message to thread: {str(e)}",
        )


async def get_user_notifications(
    db: AsyncSession,
    user: User,
    skip: int = 0,
    limit: int = 20,
    mark_read: bool = False,
) -> NotificationListResponseSchema:
    """Get notifications for a specific user"""
    try:
        # Get individual notifications
        individual_query = select(Notification).where(
            Notification.recipient_id == user.id
        )

        # Get broadcast notifications where user is a recipient
        broadcast_query = (
            select(Notification)
            .join(NotificationRecipient)
            .where(
                and_(
                    NotificationRecipient.recipient_id == user.id,
                    Notification.is_broadcast == True,
                )
            )
        )

        # Get report thread notifications where user is involved
        report_query = (
            select(Notification)
            .join(ReportIssue)
            .where(
                or_(
                    ReportIssue.reporter_id == user.id,
                    ReportIssue.vendor_id == user.id,
                    ReportIssue.customer_id == user.id,
                    ReportIssue.dispatch_id == user.id,
                )
            )
        )

        # Combine all queries
        combined_query = individual_query.union(broadcast_query).union(report_query)

        # Get total count
        count_result = await db.execute(
            select(func.count()).select_from(combined_query.subquery())
        )
        total_count = count_result.scalar()

        # Get paginated results
        notifications_result = await db.execute(
            combined_query.options(
                selectinload(Notification.thread_messages),
                selectinload(Notification.recipients),
                selectinload(Notification.sender),
                selectinload(Notification.report_issue),
            )
            .order_by(Notification.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        notifications = notifications_result.scalars().all()

        # Mark notifications as read if requested
        if mark_read:
            for notification in notifications:
                await mark_notification_read_on_view(db, notification.id, user)

        # Get unread count
        unread_query = combined_query.where(Notification.is_read == False)
        unread_result = await db.execute(
            select(func.count()).select_from(unread_query.subquery())
        )
        unread_count = unread_result.scalar()

        # Format responses
        notification_responses = []
        for notification in notifications:
            response = await format_notification_response(db, notification)
            notification_responses.append(response)

        return NotificationListResponseSchema(
            notifications=notification_responses,
            total_count=total_count,
            unread_count=unread_count,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get notifications: {str(e)}",
        )


async def mark_notification_read(
    db: AsyncSession,
    notification_id: UUID,
    user: User,
) -> bool:
    """Mark a notification as read for a specific user"""
    try:
        # For individual notifications
        result = await db.execute(
            update(Notification)
            .where(
                and_(
                    Notification.id == notification_id,
                    Notification.recipient_id == user.id,
                )
            )
            .values(is_read=True)
        )

        # For broadcast notifications
        if result.rowcount == 0:
            result = await db.execute(
                update(NotificationRecipient)
                .where(
                    and_(
                        NotificationRecipient.notification_id == notification_id,
                        NotificationRecipient.recipient_id == user.id,
                    )
                )
                .values(is_read=True)
            )

        # For report thread notifications (where user is involved)
        if result.rowcount == 0:
            await db.execute(
                update(Notification)
                .where(
                    and_(
                        Notification.id == notification_id,
                        Notification.notification_type
                        == NotificationType.REPORT_THREAD,
                        Notification.report_issue_id.in_(
                            select(ReportIssue.id).where(
                                or_(
                                    ReportIssue.reporter_id == user.id,
                                    ReportIssue.vendor_id == user.id,
                                    ReportIssue.customer_id == user.id,
                                    ReportIssue.dispatch_id == user.id,
                                )
                            )
                        ),
                    )
                )
                .values(is_read=True)
            )

        await db.commit()
        return True

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to mark notification as read: {str(e)}",
        )


async def mark_notification_read_on_view(
    db: AsyncSession,
    notification_id: UUID,
    user: User,
) -> bool:
    """Automatically mark notification as read when user views it"""
    try:
        # Check if notification exists and user has access to it
        notification_result = await db.execute(
            select(Notification).where(Notification.id == notification_id)
        )
        notification = notification_result.scalar_one_or_none()

        if not notification:
            return False

        # Check if user has access to this notification
        has_access = False

        # Individual notification
        if notification.recipient_id == user.id:
            has_access = True

        # Broadcast notification
        elif notification.is_broadcast:
            recipient_result = await db.execute(
                select(NotificationRecipient).where(
                    and_(
                        NotificationRecipient.notification_id == notification_id,
                        NotificationRecipient.recipient_id == user.id,
                    )
                )
            )
            if recipient_result.scalar_one_or_none():
                has_access = True

        # Report thread notification
        elif (
            notification.notification_type == NotificationType.REPORT_THREAD
            and notification.report_issue
        ):
            if (
                user.id == notification.report_issue.reporter_id
                or user.id == notification.report_issue.vendor_id
                or user.id == notification.report_issue.customer_id
                or user.id == notification.report_issue.dispatch_id
            ):
                has_access = True

        if not has_access:
            return False

        # Mark as read
        return await mark_notification_read(db, notification_id, user)

    except Exception as e:
        return False


async def mark_all_notifications_read(
    db: AsyncSession,
    user: User,
) -> bool:
    """Mark all notifications as read for a user"""
    try:
        # Mark individual notifications
        await db.execute(
            update(Notification)
            .where(
                and_(
                    Notification.recipient_id == user.id, Notification.is_read == False
                )
            )
            .values(is_read=True)
        )

        # Mark broadcast notifications
        await db.execute(
            update(NotificationRecipient)
            .where(
                and_(
                    NotificationRecipient.recipient_id == user.id,
                    NotificationRecipient.is_read == False,
                )
            )
            .values(is_read=True)
        )

        await db.commit()
        return True

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to mark all notifications as read: {str(e)}",
        )


async def get_notification_stats(
    db: AsyncSession,
    user: User,
) -> NotificationStatsSchema:
    """Get notification statistics for a user"""
    try:
        # Get individual notifications count
        individual_count_result = await db.execute(
            select(func.count(Notification.id)).where(
                Notification.recipient_id == user.id
            )
        )
        individual_count = individual_count_result.scalar()

        # Get broadcast notifications count
        broadcast_count_result = await db.execute(
            select(func.count(NotificationRecipient.id))
            .join(Notification)
            .where(
                and_(
                    NotificationRecipient.recipient_id == user.id,
                    Notification.is_broadcast == True,
                )
            )
        )
        broadcast_count = broadcast_count_result.scalar()

        # Get report thread notifications count
        report_count_result = await db.execute(
            select(func.count(Notification.id))
            .join(ReportIssue)
            .where(
                or_(
                    ReportIssue.reporter_id == user.id,
                    ReportIssue.vendor_id == user.id,
                    ReportIssue.customer_id == user.id,
                    ReportIssue.dispatch_id == user.id,
                )
            )
        )
        report_count = report_count_result.scalar()

        # Get unread count
        unread_individual_result = await db.execute(
            select(func.count(Notification.id)).where(
                and_(
                    Notification.recipient_id == user.id, Notification.is_read == False
                )
            )
        )
        unread_individual = unread_individual_result.scalar()

        unread_broadcast_result = await db.execute(
            select(func.count(NotificationRecipient.id))
            .join(Notification)
            .where(
                and_(
                    NotificationRecipient.recipient_id == user.id,
                    NotificationRecipient.is_read == False,
                    Notification.is_broadcast == True,
                )
            )
        )
        unread_broadcast = unread_broadcast_result.scalar()

        total_unread = unread_individual + unread_broadcast
        total_notifications = individual_count + broadcast_count + report_count

        return NotificationStatsSchema(
            total_notifications=total_notifications,
            unread_notifications=total_unread,
            broadcast_notifications=broadcast_count,
            individual_notifications=individual_count,
            report_thread_notifications=report_count,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get notification stats: {str(e)}",
        )


async def format_notification_response(
    db: AsyncSession,
    notification: Notification,
) -> NotificationResponseSchema:
    """Format notification for response"""
    # Get sender information with profile
    sender_info = {
        "id": str(notification.sender.id),
        "name": notification.sender.profile.full_name
        or notification.sender.profile.business_name
        or "Admin",
        "email": notification.sender.email,
        "profile_image": notification.sender.profile.profile_image.profile_image_url
        if notification.sender.profile.profile_image
        else None,
    }

    # Format thread messages with sender profile info
    thread_messages = []
    for message in notification.thread_messages:
        message_data = NotificationMessageSchema.model_validate(message)
        # Add sender profile info to each message
        message_dict = message_data.dict()
        message_dict["sender"] = {
            "id": str(message.sender.id),
            "name": message.sender.profile.full_name
            or message.sender.profile.business_name
            or "Admin",
            "email": message.sender.email,
            "profile_image": message.sender.profile.profile_image.profile_image_url
            if message.sender.profile.profile_image
            else None,
        }
        thread_messages.append(message_dict)

    # Format recipients
    recipients = []
    for recipient in notification.recipients:
        recipients.append(NotificationRecipientSchema.model_validate(recipient))

    return NotificationResponseSchema(
        id=notification.id,
        notification_type=notification.notification_type,
        title=notification.title,
        content=notification.content,
        is_read=notification.is_read,
        is_broadcast=notification.is_broadcast,
        created_at=notification.created_at,
        sender=sender_info,
        thread_messages=thread_messages,
        recipients=recipients,
    )


async def send_thread_notifications(
    db: AsyncSession,
    notification: Notification,
    message: NotificationMessage,
):
    """Send push notifications to thread participants"""
    try:
        if not notification.report_issue:
            return

        notification_tokens = []

        # Add reporter
        if notification.report_issue.reporter.notification_token:
            notification_tokens.append(
                notification.report_issue.reporter.notification_token
            )

        # Add vendor if exists
        if (
            notification.report_issue.vendor
            and notification.report_issue.vendor.notification_token
        ):
            notification_tokens.append(
                notification.report_issue.vendor.notification_token
            )

        # Add customer if exists
        if (
            notification.report_issue.customer
            and notification.report_issue.customer.notification_token
        ):
            notification_tokens.append(
                notification.report_issue.customer.notification_token
            )

        # Add dispatch if exists
        if (
            notification.report_issue.dispatch
            and notification.report_issue.dispatch.notification_token
        ):
            notification_tokens.append(
                notification.report_issue.dispatch.notification_token
            )

        if notification_tokens:
            await send_push_notification(
                tokens=notification_tokens,
                title=f"New message in {notification.title}",
                message=message.content,
                navigate_to="/notifications",
            )
    except Exception as e:
        # Log error but don't fail the main operation
        print(f"Failed to send thread notifications: {str(e)}")


def determine_user_role(user: User, report_issue: ReportIssue) -> SenderRole:
    """Determine the role of a user in relation to a report issue"""
    if user.id == report_issue.reporter_id:
        return SenderRole.REPORTER
    elif user.id == report_issue.vendor_id:
        return SenderRole.REPORTEE
    elif user.id == report_issue.customer_id:
        return SenderRole.REPORTEE
    elif user.id == report_issue.dispatch_id:
        return SenderRole.REPORTEE
    elif user.user_type == "ADMIN":
        return SenderRole.ADMIN
    else:
        return SenderRole.REPORTER  # Default fallback


async def create_automatic_report_thread(
    db: AsyncSession,
    report_issue: ReportIssue,
) -> NotificationResponseSchema:
    """Automatically create a notification thread when a report issue is created"""
    try:
        # Get admin user (you might want to get the first admin or a specific admin)
        admin_result = await db.execute(
            select(User).where(User.user_type == "ADMIN").limit(1)
        )
        admin = admin_result.scalar_one_or_none()

        if not admin:
            # If no admin exists, use the reporter as sender for now
            admin = report_issue.reporter

        # Create the notification thread
        notification = Notification(
            notification_type=NotificationType.REPORT_THREAD,
            report_issue_id=report_issue.id,
            sender_id=admin.id,
            title=f"New {report_issue.issue_type.value} Report",
            content=f"A new {report_issue.issue_type.value.lower()} has been reported and is under investigation.",
            is_broadcast=False,
        )

        db.add(notification)
        await db.commit()
        await db.refresh(notification)

        # Send push notifications to relevant users
        notification_tokens = []

        # Add reporter
        if report_issue.reporter.notification_token:
            notification_tokens.append(report_issue.reporter.notification_token)

        # Add vendor if exists
        if report_issue.vendor and report_issue.vendor.notification_token:
            notification_tokens.append(report_issue.vendor.notification_token)

        # Add customer if exists
        if report_issue.customer and report_issue.customer.notification_token:
            notification_tokens.append(report_issue.customer.notification_token)

        # Add dispatch if exists
        if report_issue.dispatch and report_issue.dispatch.notification_token:
            notification_tokens.append(report_issue.dispatch.notification_token)

        if notification_tokens:
            await send_push_notification(
                tokens=notification_tokens,
                title=f"New {report_issue.issue_type.value} Report",
                message=f"A new {report_issue.issue_type.value.lower()} has been reported.",
                navigate_to="/notifications",
            )

        return await format_notification_response(db, notification)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create automatic report thread: {str(e)}",
        )


async def get_unread_badge_count(
    db: AsyncSession,
    user: User,
) -> dict:
    """Get total unread count for badge display"""
    try:
        # Individual notifications
        unread_individual_result = await db.execute(
            select(func.count(Notification.id)).where(
                and_(
                    Notification.recipient_id == user.id, Notification.is_read == False
                )
            )
        )
        unread_individual = unread_individual_result.scalar() or 0

        # Broadcast notifications
        unread_broadcast_result = await db.execute(
            select(func.count(NotificationRecipient.id))
            .join(Notification)
            .where(
                and_(
                    NotificationRecipient.recipient_id == user.id,
                    NotificationRecipient.is_read == False,
                    Notification.is_broadcast == True,
                )
            )
        )
        unread_broadcast = unread_broadcast_result.scalar() or 0

        # Report thread notifications (where user is involved)
        unread_reports_result = await db.execute(
            select(func.count(Notification.id))
            .join(ReportIssue)
            .where(
                and_(
                    or_(
                        ReportIssue.reporter_id == user.id,
                        ReportIssue.vendor_id == user.id,
                        ReportIssue.customer_id == user.id,
                        ReportIssue.dispatch_id == user.id,
                    ),
                    Notification.is_read == False,
                )
            )
        )
        unread_reports = unread_reports_result.scalar() or 0

        total_unread = unread_individual + unread_broadcast + unread_reports

        return {"unread_count": total_unread}

    except Exception as e:
        return {"unread_count": 0}


async def get_new_notifications(
    db: AsyncSession,
    user: User,
    limit: int = 5,
) -> List[dict]:
    """Get new notifications for real-time updates"""
    try:
        # Get recent unread notifications
        recent_notifications_result = await db.execute(
            select(Notification)
            .where(
                and_(
                    or_(
                        Notification.recipient_id == user.id,
                        Notification.is_broadcast == True,
                    ),
                    Notification.is_read == False,
                )
            )
            .options(
                selectinload(Notification.sender),
                selectinload(Notification.thread_messages),
                selectinload(Notification.recipients),
            )
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )

        notifications = recent_notifications_result.scalars().all()

        # Format notifications for SSE
        formatted_notifications = []
        for notification in notifications:
            formatted_notification = {
                "id": str(notification.id),
                "type": notification.notification_type,
                "title": notification.title,
                "content": notification.content,
                "created_at": notification.created_at.isoformat(),
                "sender": {
                    "id": str(notification.sender.id),
                    "name": notification.sender.profile.full_name
                    or notification.sender.profile.business_name
                    or "Admin",
                    "profile_image": notification.sender.profile.profile_image.profile_image_url
                    if notification.sender.profile.profile_image
                    else None,
                },
            }
            formatted_notifications.append(formatted_notification)

        return formatted_notifications

    except Exception as e:
        return []


async def stream_notifications(
    db: AsyncSession,
    user: User,
):
    """Stream real-time notifications via SSE"""
    try:
        while True:
            # Get new notifications
            new_notifications = await get_new_notifications(db, user)

            # Get unread count
            badge_data = await get_unread_badge_count(db, user)

            # Yield notification events
            if new_notifications:
                yield {"event": "notification", "data": json.dumps(new_notifications)}

            # Yield badge count updates
            yield {"event": "unread_count", "data": json.dumps(badge_data)}

            await asyncio.sleep(5)  # Check every 5 seconds

    except Exception as e:
        # Log error but don't break the stream
        print(f"SSE stream error: {str(e)}")
        yield {
            "event": "error",
            "data": json.dumps({"message": "Stream error occurred"}),
        }


async def mark_thread_messages_read(
    db: AsyncSession,
    notification_id: UUID,
    user: User,
) -> bool:
    """Mark all messages in a notification thread as read for a user"""
    try:
        # Check if user has access to this notification
        notification_result = await db.execute(
            select(Notification).where(Notification.id == notification_id)
        )
        notification = notification_result.scalar_one_or_none()

        if not notification:
            return False

        # Check access for report thread notifications
        has_access = False
        if (
            notification.notification_type == NotificationType.REPORT_THREAD
            and notification.report_issue
        ):
            if (
                user.id == notification.report_issue.reporter_id
                or user.id == notification.report_issue.vendor_id
                or user.id == notification.report_issue.customer_id
                or user.id == notification.report_issue.dispatch_id
            ):
                has_access = True

        if not has_access:
            return False

        # Mark all unread messages in the thread as read
        await db.execute(
            update(NotificationMessage)
            .where(
                and_(
                    NotificationMessage.notification_id == notification_id,
                    NotificationMessage.is_read == False,
                    NotificationMessage.sender_id
                    != user.id,  # Don't mark own messages as read
                )
            )
            .values(is_read=True)
        )

        await db.commit()
        return True

    except Exception as e:
        await db.rollback()
        return False
