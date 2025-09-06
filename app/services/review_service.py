import asyncio
import datetime
import json

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, or_, select, update, insert, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException


from app.schemas.review_schema import (
    BadgeCount,
    FilteredReviewsResponse,
    MessageCreate,
    MessageType,
    ReportCreate,
    ReportMessage,
    ReportIssueUpdate,
    ReportTag,
    ReviewCreate,
    ReviewFilter,
    ReviewStats,
    ReviewerProfile,
    ReviewType,
    ReportedUserType,
    StatusUpdate,
    ReviewResponse,
    ReportType,
    ReportStatus,
    ReportIssueResponse,
    ReportResponseSchema,
    ThreadMessage,
    SenderInfo,
)
from app.models.models import (
    AuditLog,
    Message,
    MessageReadStatus,
    User,
    Review,
    Order,
    UserReport,
    Profile,
    UserReportReadStatus,
)
from app.schemas.status_schema import OrderStatus, UserType
from app.config.config import redis_client, settings, channel, server_client

# from app.services.notification_service import create_automatic_report_thread

from uuid import UUID

from app.utils.utils import (
    get_full_name_or_business_name,
    get_user_notification_token,
    send_push_notification,
)
from app.ws_manager.ws_manager import manager

ADMIN_MESSAGE = f"""

Hello and thank you for bringing this to our attention. We've received your report and our support team has started an investigation.

To keep everyone informed, we've created a dedicated thread for this case. This shared space will include you and the other user, allowing for a clear and centralized dialogue as we work towards a resolution.

Please know that we are reviewing everything carefully. We will post all updates and our final decision within this thread and will notify you accordingly. We thank you for your help in keeping our community safe and respectful.
"""


def convert_report_to_response(report: ReportType) -> ReportIssueResponse:
    """Convert a ReportIssue SQLAlchemy model to ReportIssueResponse Pydantic model"""
    return ReportIssueResponse(
        id=report.id,
        order_id=report.order_id,
        # delivery_id=report.delivery_id,
        dispatch_id=report.dispatch_id,
        vendor_id=report.vendor_id,
        customer_id=report.customer_id,
        reporter_id=report.reporter_id,
        description=report.description,
        issue_type=report.issue_type,
        issue_status=report.issue_status,
        reporting=report.reporting,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


async def create_review(
    db: AsyncSession, current_user: User, data: ReviewCreate
) -> ReviewCreate:
    """Creates a review for a completed food or laundry order."""
    # 1. Fetch the order and verify its existence
    order_result = await db.execute(select(Order).where(Order.id == data.order_id))
    order = order_result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )

    # 2. Perform all validation checks
    if order.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only review an order you placed.",
        )

    if order.vendor_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot review your own order.",
        )

    if order.order_status != OrderStatus.RECEIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order must be in 'RECEIVED' status to be reviewed.",
        )

    # 3. Check if a review already exists for this order by this user
    existing_review_result = await db.execute(
        select(Review).where(
            Review.order_id == data.order_id, Review.reviewer_id == current_user.id
        )
    )
    if existing_review_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reviewed this order.",
        )

    # 4. Create and save the review
    try:
        review = Review(
            order_id=data.order_id,
            reviewee_id=order.vendor_id,
            reviewer_id=current_user.id,
            rating=data.rating,
            comment=data.comment,
            review_type=ReviewType.ORDER,
        )

        db.add(review)
        await db.commit()
        await db.refresh(review)

        redis_client.delete(f"reviews:{order.vendor_id}")

        return ReviewResponse(
            id=review.id,
            rating=review.rating,
            comment=review.comment,
            created_at=review.created_at
        )

    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the review due to a database error.",
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}",
        )


async def create_product_review(
    db: AsyncSession, current_user: User, data: ReviewCreate
) -> ReviewResponse:
    """Creates a review for a purchased product."""
    # 1. Fetch the order with its items and verify existence
    order_result = await db.execute(
        select(Order)
        .where(Order.id == data.order_id)
        .options(selectinload(Order.order_items))
    )
    order = order_result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found.",
        )

    # 2. Perform all validation checks
    if order.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only review an order you placed.",
        )

    if order.vendor_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot review your own item.",
        )

    if order.order_status != OrderStatus.RECEIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order must be in 'RECEIVED' status to be reviewed.",
        )

    if not order.order_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot review an order with no items.",
        )

    # 3. Check if a review already exists for this order by this user
    existing_review_result = await db.execute(
        select(Review).where(
            Review.order_id == data.order_id, Review.reviewer_id == current_user.id
        )
    )
    if existing_review_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reviewed this order.",
        )

    # 4. Create and save the review
    try:
       
        item_to_review = order.order_items[0]

        review = Review(
            order_id=data.order_id,
            item_id=item_to_review.item_id,
            reviewee_id=order.vendor_id,
            reviewer_id=current_user.id,
            rating=data.rating,
            comment=data.comment,
            review_type=ReviewType.PRODUCT,
        )

        db.add(review)
        await db.commit()
        await db.refresh(review)

        cache_key = f"reviews:{review.item_id}"
        redis_client.delete(cache_key)

        return ReviewResponse(
            id=review.id,
            rating=review.rating,
            comment=review.comment,
            created_at=review.created_at
        )

    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the review due to a database error.",
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}",
        )


async def fetch_vendor_reviews(
    vendor_id: UUID, db: AsyncSession
) -> list[ReviewResponse]:
    cache_key = f"reviews:{vendor_id}"
    cached_reviews = redis_client.get(cache_key)
    if cached_reviews:
        # Parse the JSON string back to a list of dictionaries
        reviews_data = json.loads(cached_reviews)
        return [ReviewResponse(**r) for r in reviews_data]
    
    # DB fallback
    stmt = (
        select(Review)
        .options(
            selectinload(Review.reviewer)
            .selectinload(User.profile)
            .selectinload(Profile.profile_image)
        )
        .where(Review.reviewee_id == vendor_id)
    )
    stmt = stmt.order_by(Review.created_at.desc())
    result = await db.execute(stmt)
    reviews = result.scalars().all()
    response_list = [
        ReviewResponse(
            id=r.id,
            rating=r.rating,
            comment=r.comment,
            created_at=r.created_at,
            reviewer=ReviewerProfile(
                id=r.reviewer.id,
                full_name=r.reviewer.profile.full_name
                if r.reviewer.profile and r.reviewer.profile.full_name
                else r.reviewer.profile.business_name
                if r.reviewer.profile
                else None,
                profile_image_url=(
                    r.reviewer.profile.profile_image.profile_image_url
                    if r.reviewer.profile and r.reviewer.profile.profile_image
                    else None
                ),
            ),
        )
        for r in reviews
    ]
    # Only cache if we have a full page
    if response_list:
        value = json.dumps([r.model_dump() for r in response_list], default=str)
        redis_client.setex(cache_key, 3600, value) 
    return response_list





async def fetch_item_reviews(
    item_id: UUID, db: AsyncSession
) -> list[ReviewResponse]:

    cache_key = f"reviews:{item_id}"
    cached_reviews = redis_client.get(cache_key)

    if cached_reviews:
        # Parse the JSON string back to a list of dictionaries
        reviews_data = json.loads(cached_reviews)
        return [ReviewResponse(**r) for r in reviews_data]

    # DB fallback
    stmt = (
        select(Review)
        .options(
            selectinload(Review.reviewer)
            .selectinload(User.profile)
            .selectinload(Profile.profile_image)
        )
        .where(Review.item_id == item_id)
    )

    stmt = stmt.order_by(Review.created_at.desc())
    result = await db.execute(stmt)
    reviews = result.scalars().all()

    response_list = [
        ReviewResponse(
            id=r.id,
            rating=r.rating,
            comment=r.comment,
            created_at=r.created_at,
            reviewer=ReviewerProfile(
                id=r.reviewer.id,
                full_name=r.reviewer.profile.full_name
                if r.reviewer.profile and r.reviewer.profile.full_name
                else r.reviewer.profile.business_name
                if r.reviewer.profile
                else None,
                profile_image_url=(
                    r.reviewer.profile.profile_image.profile_image_url
                    if r.reviewer.profile and r.reviewer.profile.profile_image
                    else None
                ),
            ),
        )
        for r in reviews
    ]

    # Only cache if we have a full page
    if response_list:
        redis_client.setex(
            cache_key,
            settings.REDIS_EX,
            json.dumps([r.model_dump() for r in response_list], default=str),
        )
       

    return response_list


async def create_report(
    db: AsyncSession, order_id: UUID, current_user: User, report_data: ReportCreate
) -> ReportResponseSchema:
    """Create a new report with automatic admin acknowledgment message."""

    # 1. Fetch order and an admin user sequentially to avoid concurrent use of the same session
    order_stmt = (
        select(Order)
        .options(selectinload(Order.delivery))
        .where(Order.id == order_id)
    )
    order_result = await db.execute(order_stmt)
    order = order_result.scalar_one_or_none()

    admin_stmt = select(User).where(User.user_type == UserType.MODERATOR).limit(1)
    admin_result = await db.execute(admin_stmt)
    admin_user = admin_result.scalar_one_or_none()

    # 2. Perform all validations before proceeding
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with ID {order_id} not found",
        )

    if not admin_user:
        # This is a system configuration issue, not a client error.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="System configuration error: No admin user available to handle reports.",
        )

    # Determine defendant and other report details
    defendant_id = None
    if report_data.reported_user_type == ReportedUserType.CUSTOMER:
        defendant_id = order.owner_id
    elif report_data.reported_user_type == ReportedUserType.VENDOR:
        defendant_id = order.vendor_id
    elif report_data.reported_user_type == ReportedUserType.DISPATCH:
        if not order.delivery:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order has no delivery information for a dispatch report.",
            )
        defendant_id = order.delivery.dispatch_id

    if not defendant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not identify the defendant for the report type '{report_data.reported_user_type.value}'.",
        )
    
    if current_user.id == defendant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot report yourself."
        )

    # Proactively check for an existing report to provide a clear error message
    existing_report_conditions = [
        UserReport.complainant_id == current_user.id,
        UserReport.defendant_id == defendant_id,
        UserReport.order_id == order_id,
    ]
    if order_id:
        # For dispatch reports, the uniqueness is on the delivery
        existing_report_conditions.append(UserReport.order_id == order_id)

    existing_report_stmt = select(UserReport).where(and_(*existing_report_conditions))
    if (await db.execute(existing_report_stmt)).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already submitted a report for this issue. Please check your existing reports or contact support.",
        )

    # 3. Create report and related records within a single transaction
    try:
        report = UserReport(
            reported_user_type=report_data.reported_user_type,
            report_type=report_data.report_type,
            description=report_data.description,
            complainant_id=current_user.id,
            defendant_id=defendant_id,
            order_id=order_id,
            report_tag=ReportTag.COMPLAINANT,
            report_status=ReportStatus.INVESTIGATING,
        )
        db.add(report)
        await db.flush()

        # Create read statuses for both complainant and defendant
        db.add_all(
            [
                UserReportReadStatus(
                    report_id=report.id, user_id=current_user.id, is_read=True
                ),
                UserReportReadStatus(
                    report_id=report.id, user_id=defendant_id, is_read=False
                ),
            ]
        )

        # Create admin acknowledgment message with the admin as the sender
        admin_message = Message(
            message_type=MessageType.REPORT,
            content=ADMIN_MESSAGE,
            report_id=report.id,
            sender_id=admin_user.id,
            role=UserType.MODERATOR,
        )
        db.add(admin_message)

        await db.commit()
        await db.refresh(report)

    except IntegrityError as e:
        await db.rollback()
        # This is a fallback; the proactive check should catch most cases.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A report with these details already exists. {e}",
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while creating the report: {e}",
        )

    # 4. Post-commit actions (like sending notifications)
    try:
        token = await get_user_notification_token(db=db, user_id=report.defendant_id)
        if token:
            await send_push_notification(
                tokens=[token],
                title="Dispute Opened",
                message="A report has been filed regarding a recent transaction. Please check your reports section for details.",
                navigate_to="/(app)/reports",  # More specific navigation
            )
    except Exception:
        # Don't fail the request if notification fails, just log it.
        pass

    return report


async def add_message_to_report(
    db: AsyncSession, report_id: UUID, current_user: User, message_data: MessageCreate
) -> MessageCreate:
    """
    Add a message to an existing report thread, making it feel real-time with
    WebSockets and push notifications.
    """
    from app.services.ws_service import broadcast_new_report_message

    async with db.begin():
        # 1. Fetch report and verify user is a participant
        report_stmt = (
            select(UserReport)
            .options(
                selectinload(UserReport.complainant)
                .selectinload(User.profile)
                .selectinload(Profile.profile_image),
                selectinload(UserReport.defendant)
                .selectinload(User.profile)
                .selectinload(Profile.profile_image),
                selectinload(UserReport.order),  # For notification context
            )
            .where(UserReport.id == report_id)
        )
        report_result = await db.execute(report_stmt)
        report = report_result.scalar_one_or_none()

        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Report not found."
            )

        is_admin = current_user.user_type in [
            UserType.ADMIN,
            UserType.SUPER_ADMIN,
            UserType.MODERATOR,
        ]
        is_participant = current_user.id in [
            report.complainant_id,
            report.defendant_id,
        ]

        if not (is_participant or is_admin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to post in this report thread.",
            )

        # 2. Create and save the message
        message_obj = Message(
            message_type=MessageType.REPORT,
            content=message_data.content,
            sender_id=current_user.id,
            report_id=report_id,
            role=current_user.user_type,
        )
        db.add(message_obj)
        await db.flush()

        # 3. Mark the report as unread for all other participants
        recipient_users = []
        if current_user.id != report.complainant_id:
            recipient_users.append(report.complainant)
        if current_user.id != report.defendant_id:
            recipient_users.append(report.defendant)

        # Mark as unread for direct recipients
        for recipient in recipient_users:
            await db.execute(
                update(UserReportReadStatus)
                .where(
                    UserReportReadStatus.report_id == report_id,
                    UserReportReadStatus.user_id == recipient.id,
                )
                .values(is_read=False)
            )

    # Refresh the message object to get DB-defaults like created_at
    await db.refresh(message_obj)

    # 4. Invalidate relevant caches
    cache_keys_to_delete = [
        f"report:{report_id}:thread:{uid}"
        for uid in [report.complainant_id, report.defendant_id, current_user.id]
    ]
    redis_client.delete(*cache_keys_to_delete)

    # 5. Prepare rich payload and send real-time updates
    sender_name = await get_full_name_or_business_name(db, current_user.id)
    sender_avatar = (
        current_user.profile.profile_image.profile_image_url
        if current_user.profile and current_user.profile.profile_image
        else None
    )

    thread_message_payload = ThreadMessage(
        id=message_obj.id,
        sender=SenderInfo(name=sender_name, avatar=sender_avatar),
        message_type=message_obj.message_type,
        role=str(current_user.user_type.value),
        date=message_obj.created_at,
        content=message_obj.content,
        read=False,  # It's unread for the recipients
    )

    # Broadcast via WebSocket to direct participants and all admins
    recipient_ids_for_ws = [str(u.id) for u in recipient_users]
    await broadcast_new_report_message(
        report_id=str(report.id),
        message_data=thread_message_payload.model_dump(mode="json"),
        recipient_ids=recipient_ids_for_ws,
    )

    # Send push notifications to direct recipients
    for recipient in recipient_users:
        # Only send a push notification if the user is NOT connected via WebSocket
        is_recipient_online = await manager.is_user_online(str(recipient.id))
        if not is_recipient_online:
            try:
                token = await get_user_notification_token(db=db, user_id=recipient.id)
                if token:
                    await send_push_notification(
                        tokens=[token],
                        title=f"New Message in Report #{report.order.order_number if report.order else report.id}",
                        message=f"{sender_name}: {message_data.content[:50]}...",
                        navigate_to=f"/(app)/reports/{report.id}",
                    )
            except Exception:
                # Log and continue, don't fail the request if a notification fails
                pass

    return message_data


async def mark_message_as_read(db: AsyncSession, report_id: UUID, current_user: User):
    """Mark a report and all its messages as read for a specific user"""
    # Mark the report as read for this user
    await db.execute(
        update(UserReportReadStatus)
        .where(
            UserReportReadStatus.report_id == report_id,
            UserReportReadStatus.user_id == current_user.id,
        )
        .values(is_read=True)
    )
    # Mark all messages in the thread as read for this user
    await mark_thread_as_read_for_user(db, report_id, current_user)
    await db.commit()


async def get_user_messages(db: AsyncSession, user_id: UUID) -> list[ReportMessage]:
    cache_key = f"user:{user_id}:report_threads"
    cached = redis_client.get(cache_key)
    if cached:
        try:
            return [ReportMessage.model_validate(msg) for msg in json.loads(cached)]
        except Exception:
            redis_client.delete(cache_key)
    # Get reports where user is involved (complainant or defendant)
    reports_stmt = (
        select(UserReport)
        .options(
            selectinload(UserReport.messages).selectinload(Message.sender),
            selectinload(UserReport.messages).selectinload(Message.read_status),
            selectinload(UserReport.complainant),
            selectinload(UserReport.defendant),
        )
        .where(
            or_(
                UserReport.complainant_id == user_id, UserReport.defendant_id == user_id
            )
        )
        .order_by(UserReport.created_at.desc())
    )
    reports_result = await db.execute(reports_stmt)
    reports = reports_result.scalars().all()

    report_messages = []
    for report in reports:
        thread = []
        for msg in sorted(report.messages, key=lambda x: x.created_at):
            sender_name = None
            sender_avatar = None
            if msg.sender and msg.sender.profile:
                sender_name = (
                    msg.sender.profile.full_name
                    or msg.sender.profile.business_name
                    or "Admin"
                )
                if msg.sender.profile.profile_image:
                    sender_avatar = msg.sender.profile.profile_image.profile_image_url
            sender_info = SenderInfo(name=sender_name or "User", avatar=sender_avatar)
            read_status = next(
                (rs for rs in msg.read_status if rs.user_id == user_id), None
            )
            is_msg_read = read_status.read if read_status else False
            thread.append(
                ThreadMessage(
                    id=msg.id,
                    sender=sender_info,
                    message_type=msg.message_type,
                    role=msg.role.value if msg.role else None,
                    date=msg.created_at,
                    content=msg.content,
                    read=is_msg_read,
                )
            )
        report_messages.append(
            ReportMessage(
                id=report.id,
                complainant_id=report.complainant_id,
                report_type=report.reported_user_type,
                report_tag=report.report_tag,
                report_status=report.report_status,
                description=report.description,
                is_read=report.is_read,
                created_at=report.created_at,
                thread=thread,
            )
        )
    redis_client.setex(
        cache_key,
        settings.REDIS_EX,
        json.dumps([msg.model_dump() for msg in report_messages], default=str),
    )
    return report_messages


async def get_all_report_messages_for_admin(db: AsyncSession) -> list[ReportMessage]:
    """
    Fetch all report threads/messages for admin use (not filtered by user).
    Returns a list of ReportMessage objects for all reports in the system.
    """
    reports_stmt = (
        select(UserReport)
        .options(
            selectinload(UserReport.messages).selectinload(Message.sender),
            selectinload(UserReport.messages).selectinload(Message.read_status),
            selectinload(UserReport.complainant),
            selectinload(UserReport.defendant),
        )
        .order_by(UserReport.created_at.desc())
    )
    reports_result = await db.execute(reports_stmt)
    reports = reports_result.scalars().all()

    report_messages = []
    for report in reports:
        thread = []
        for msg in sorted(report.messages, key=lambda x: x.created_at):
            sender_name = None
            sender_avatar = None
            if msg.sender and msg.sender.profile:
                sender_name = (
                    msg.sender.profile.full_name
                    or msg.sender.profile.business_name
                    or "Admin"
                )
                if msg.sender.profile.profile_image:
                    sender_avatar = msg.sender.profile.profile_image.profile_image_url
            sender_info = SenderInfo(name=sender_name or "User", avatar=sender_avatar)
            # For admin, mark all as read=False (or you can add logic if needed)
            thread.append(
                ThreadMessage(
                    id=msg.id,
                    sender=sender_info,
                    message_type=msg.message_type,
                    role=msg.role.value if msg.role else None,
                    date=msg.created_at,
                    content=msg.content,
                    read=False,
                )
            )
        report_messages.append(
            ReportMessage(
                id=report.id,
                complainant_id=report.complainant_id,
                report_type=report.reported_user_type,
                report_tag=report.report_tag,
                report_status=report.report_status,
                description=report.description,
                is_read=report.is_read,
                created_at=report.created_at,
                thread=thread,
            )
        )
    return report_messages


async def mark_thread_as_read_for_user(db: AsyncSession, report_id: UUID, user: User):
    # Get all messages in the report thread
    stmt = select(Message).where(Message.report_id == report_id)
    result = await db.execute(stmt)
    messages = result.scalars().all()

    for msg in messages:
        stmt = select(MessageReadStatus).where(
            MessageReadStatus.message_id == msg.id,
            MessageReadStatus.user_id == user.id,
        )
        rs_result = await db.execute(stmt)
        read_status = rs_result.scalar_one_or_none()
        if read_status:
            if not read_status.read:
                read_status.read = True
                read_status.read_at = datetime.datetime.now()
        else:
            db.add(
                MessageReadStatus(
                    message_id=msg.id,
                    user_id=user.id,
                    read=True,
                    read_at=datetime.datetime.now(),
                )
            )

    # Set report.is_read = True
    await db.execute(
        update(UserReport).where(UserReport.id == report_id).values(is_read=True)
    )
    await db.commit()


async def delete_report_if_allowed(
    db: AsyncSession, report_id: UUID, current_user: User
) -> None:
    """Delete a report and its thread if status is dismissed or resolved."""
    stmt = select(UserReport).where(UserReport.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )

    if report.report_status not in [ReportStatus.DISMISSED, ReportStatus.RESOLVED]:
        raise HTTPException(
            status_code=400,
            detail="Can only delete reports that are dismissed or resolved.",
        )

    await db.delete(report)
    await db.commit()
    # Invalidate cache for both users
    redis_client.delete(f"user:{report.complainant_id}:report_threads")
    redis_client.delete(f"user:{report.defendant_id}:report_threads")
    redis_client.delete(f"report:{report_id}:thread:{report.complainant_id}")
    redis_client.delete(f"report:{report_id}:thread:{report.defendant_id}")
    # --- AUDIT LOG ---
    audit = AuditLog(
        actor_id=current_user.id,
        actor_name=getattr(current_user, "email", "unknown"),
        actor_role=str(getattr(current_user, "user_type", "unknown")),
        action="delete_report",
        resource_type="UserReport",
        resource_id=report_id,
        resource_summary=f"Updated report with ID {report_id}",
        changes=None,
        metadata=None,
    )
    db.add(audit)
    await db.commit()
    return None


async def update_report_status(
    db: AsyncSession, report_id: UUID, new_status: StatusUpdate, current_user: User
) -> StatusUpdate:
    """Update the status of a report. Only admin or the complainant can update."""
    stmt = select(UserReport).where(UserReport.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )

    # Allow only admin or the complainant to update
    is_admin = getattr(current_user, "user_type", None) in [
        UserType.ADMIN,
        UserType.MODERATOR,
        UserType.SUPER_ADMIN,
    ]
    if current_user.id != report.complainant_id and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this report.",
        )

    old_status = report.report_status
    report.report_status = new_status.report_status
    await db.commit()
    await db.refresh(report)
    # Invalidate cache for both users
    redis_client.delete(f"user:{report.complainant_id}:report_threads")
    redis_client.delete(f"user:{report.defendant_id}:report_threads")
    redis_client.delete(f"report:{report_id}:thread:{report.complainant_id}")
    redis_client.delete(f"report:{report_id}:thread:{report.defendant_id}")

    # --- AUDIT LOG ---
    audit = AuditLog(
        actor_id=current_user.id,
        actor_name=getattr(current_user, "email", "unknown"),
        actor_role=str(getattr(current_user, "user_type", "unknown")),
        action="update_report_status",
        resource_type="UserReport",
        resource_id=report_id,
        resource_summary=str(report_id),
        changes={"report_status": [old_status, new_status]},
        metadata=None,
    )
    db.add(audit)
    await db.commit()
    return ReportIssueUpdate.model_validate(new_status)


async def get_report_by_id(
    db: AsyncSession, current_user: User, report_id: UUID
) -> ReportMessage:
    cache_key = f"report:{report_id}:thread:{current_user.id}"
    cached = redis_client.get(cache_key)
    if cached:
        try:
            return ReportMessage.model_validate(json.loads(cached))
        except Exception:
            redis_client.delete(cache_key)
    stmt = (
        select(UserReport)
        .options(
            selectinload(UserReport.messages).selectinload(Message.sender),
            selectinload(UserReport.messages).selectinload(Message.read_status),
            selectinload(UserReport.complainant),
            selectinload(UserReport.defendant),
        )
        .where(UserReport.id == report_id)
    )
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found"
        )
    if current_user.id not in [
        report.complainant_id,
        report.defendant_id,
        UserType.ADMIN,
    ]:
        raise HTTPException(
            status_code=403, detail="Not authorized to view this report."
        )
    thread = []
    for msg in sorted(report.messages, key=lambda x: x.created_at):
        sender_name = None
        sender_avatar = None
        if msg.sender and msg.sender.profile:
            sender_name = (
                msg.sender.profile.full_name
                or msg.sender.profile.business_name
                or "User"
            )
            if msg.sender.profile.profile_image:
                sender_avatar = msg.sender.profile.profile_image.profile_image_url
        sender_info = SenderInfo(name=sender_name or "User", avatar=sender_avatar)
        read_status = next(
            (rs for rs in msg.read_status if rs.user_id == current_user.id), None
        )
        is_msg_read = read_status.read if read_status else False
        thread.append(
            ThreadMessage(
                id=msg.id,
                sender=sender_info,
                message_type=msg.message_type,
                role=msg.role.value if msg.role else None,
                date=msg.created_at,
                content=msg.content,
                read=is_msg_read,
            )
        )
    report_message = ReportMessage(
        id=report.id,
        complainant_id=report.complainant_id,
        report_type=report.reported_user_type,
        report_tag=report.report_tag,
        report_status=report.report_status,
        description=report.description,
        is_read=report.is_read,
        created_at=report.created_at,
        thread=thread,
    )
    redis_client.setex(cache_key, settings.REDIS_EX, report_message.model_dump_json())
    return report_message


async def get_unread_badge_count(db: AsyncSession, user_id: UUID) -> BadgeCount:
    """Return the count of unread reports for the current user (report threads)."""
    unread_stmt = select(UserReportReadStatus).where(
        UserReportReadStatus.user_id == user_id, UserReportReadStatus.is_read == False
    )
    unread_result = await db.execute(unread_stmt)
    unread_count = len(unread_result.fetchall())
    return {"unread_count": unread_count}


async def get_filtered_reviews_and_stats(
    db: AsyncSession,
    review_filter: ReviewFilter | None = None,
    page: int = 1,
    page_size: int = 20,
) -> FilteredReviewsResponse:
    """
    Get reviews based on a filter and return statistics.
    """
    # Base query
    base_query = select(Review).options(
        selectinload(Review.reviewer)
        .selectinload(User.profile)
        .selectinload(Profile.profile_image)
    )

    # Filtering logic
    if review_filter:
        if review_filter == ReviewFilter.POSITIVE:
            base_query = base_query.where(Review.rating >= 4)
        elif review_filter == ReviewFilter.NEGATIVE:
            base_query = base_query.where(Review.rating <= 2)
        elif review_filter == ReviewFilter.AVERAGE:
            base_query = base_query.where(Review.rating == 3)

    # Pagination
    offset = (page - 1) * page_size
    paginated_query = (
        base_query.order_by(Review.created_at.desc()).limit(page_size).offset(offset)
    )

    result = await db.execute(paginated_query)
    reviews = result.scalars().all()

    response_list = [
        ReviewResponse(
            id=r.id,
            rating=r.rating,
            comment=r.comment,
            created_at=r.created_at,
            reviewer=ReviewerProfile(
                id=r.reviewer.id,
                full_name=r.reviewer.profile.full_name
                if r.reviewer.profile and r.reviewer.profile.full_name
                else r.reviewer.profile.business_name
                if r.reviewer.profile
                else None,
                profile_image_url=(
                    r.reviewer.profile.profile_image.profile_image_url
                    if r.reviewer.profile and r.reviewer.profile.profile_image
                    else None
                ),
            ),
        )
        for r in reviews
    ]

    # Stats calculation
    positive_reviews_count = await db.scalar(
        select(func.count(Review.id)).where(Review.rating >= 4)
    )
    negative_reviews_count = await db.scalar(
        select(func.count(Review.id)).where(Review.rating <= 2)
    )
    average_reviews_count = await db.scalar(
        select(func.count(Review.id)).where(Review.rating == 3)
    )
    total_reviews_count = await db.scalar(select(func.count(Review.id)))

    stats = ReviewStats(
        positive_reviews=positive_reviews_count,
        negative_reviews=negative_reviews_count,
        average_reviews=average_reviews_count,
        total_reviews=total_reviews_count,
    )

    return FilteredReviewsResponse(reviews=response_list, stats=stats)
