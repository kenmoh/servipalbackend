import datetime
import json

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select, and_, update, insert
from sqlalchemy.orm import selectinload


from app.schemas.review_schema import (
    BadgeCount,
    MessageCreate,
    MessageType,
    ReportCreate,
    ReportMessage,
    ReportTag,
    ReviewCreate,
    ReviewResponse,
    ReviewerProfile,
    ReviewType,
    ReportedUserType,
    StatusUpdate,
    VendorReviewResponse,
    ReportType,
    ReportStatus,
    ReportIssueResponse,
    ReportResponseSchema,
    ThreadMessage,
    SenderInfo
)
from app.models.models import (
    Message,
    MessageReadStatus,
    User,
    Review,
    Delivery,
    Order,
    UserReport,
    Profile,
)
from app.schemas.status_schema import DeliveryStatus, OrderStatus, UserType
from app.config.config import redis_client, settings

# from app.services.notification_service import create_automatic_report_thread

from sqlalchemy.exc import NoResultFound
from uuid import UUID


def convert_report_to_response(report: ReportType) -> ReportIssueResponse:
    """Convert a ReportIssue SQLAlchemy model to ReportIssueResponse Pydantic model"""
    return ReportIssueResponse(
        id=report.id,
        order_id=report.order_id,
        delivery_id=report.delivery_id,
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
) -> ReviewResponse:
    # Shared variables
    reviewer_id = current_user.id
    reviewee_id = data.reviewee_id

    order_result = await db.execute(select(Order).where(Order.id == data.order_id))
    order = order_result.scalar_one_or_none()

    # if data.review_type == ReviewType.ORDER:
    if order.order_type == ReviewType.ORDER:
        # Optional caching
        # cache_key = f"review:order:{data.order_id}:user:{reviewer_id}"
        # if redis_client.get(cache_key):
        #     print('FROM CACHE')
        #     raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Review already exists for this order.")

        # Check if review already exists
        existing_review = await db.execute(
            select(Review).where(
                Review.order_id == data.order_id, Review.reviewer_id == reviewer_id
            )
        )
        if existing_review.scalar():
            #     redis_client.setex(cache_key, True, settings.REDIS_EX)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Review already exists for this order.",
            )

        # Order validation
        order = await db.get(Order, data.order_id)
        if not order or order.order_status != OrderStatus.RECEIVED:
            raise HTTPException(
                status_code=400, detail="Order is not completed or doesn't exist."
            )

        review = Review(
            order_id=data.order_id,
            reviewer_id=reviewer_id,
            reviewee_id=reviewee_id,
            rating=data.rating,
            comment=data.comment,
            review_type=ReviewType.ORDER,
        )

    elif order.order_type == ReviewType.PRODUCT:
        # cache_key = f"review:delivery:{data.item_id}:user:{reviewer_id}"
        # if redis_client.get(cache_key):
        #     raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Review already exists for this delivery.")

        existing_review = await db.execute(
            select(Review).where(
                Review.item_id == data.item_id, Review.reviewer_id == reviewer_id
            )
        )
        if existing_review.scalar():
            # redis_client.setex(cache_key, settings.REDIS_EX)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Review already exists for this delivery.",
            )

        delivery = await db.get(Delivery, data.delivery_id)
        if not delivery or delivery.delivery_status != DeliveryStatus.RECEIVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Delivery is not completed or doesn't exist.",
            )

        review = Review(
            item_id=data.item_id,
            reviewer_id=reviewer_id,
            reviewee_id=reviewee_id,
            rating=data.rating,
            comment=data.comment,
            review_type=ReviewType.PRODUCT,
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid review type."
        )

    # Save review
    db.add(review)
    await db.commit()
    await db.refresh(review)

    return review


async def fetch_vendor_reviews(
    vendor_id: UUID, db: AsyncSession, current_user: User
) -> list[VendorReviewResponse]:
    cache_key = f"reviews:{vendor_id}"
    cached_reviews = redis_client.get(cache_key)

    if cached_reviews:
        return [VendorReviewResponse(**r) for r in cached_reviews]

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
        VendorReviewResponse(
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
        await redis_client.setex(
            cache_key, [r.model_dump() for r in response_list], default=str
        )

    return response_list



# async def create_report(
#     db: AsyncSession, order_id: UUID, current_user: User, report_data: ReportCreate
# ) -> ReportResponseSchema:
#     """Create a new report with automatic admin acknowledgment message"""
#     order_stmt = (
#         select(Order)
#         .options(
#             selectinload(Order.delivery),
#         )
#         .where(Order.id == order_id)
#     )

#     order_result = await db.execute(order_stmt)
#     order = order_result.scalar_one_or_none()

#     defendant_id = (
#         order.owner_id
#         if report_data.reported_user_type == ReportedUserType.CUSTOMER
#         else order.vendor_id
#         if report_data.reported_user_type == ReportedUserType.VENDOR
#         else order.delivery.dispatch_id
#     )
#     # delivery_id = (
#     #     order.delivery.id
#     #     if report_data.reported_user_type == ReportedUserType.DISPATCH
#     #     else None
#     # )

#     # Create the report
#     report = UserReport(
#         reported_user_type=report_data.reported_user_type,
#         report_type=report_data.report_type,
#         description=report_data.description,
#         complainant_id=current_user.id,
#         defendant_id=defendant_id,
#         # delivery_id=delivery_id,
#         order_id=order_id,
#         report_tag=ReportTag.COMPLAINANT,
#         report_status=ReportStatus.PENDING
#     )
#     db.add(report)
#     await db.flush()  # Get the report ID

#     # Auto-generate admin acknowledgment message
#     admin_message = Message(
#         message_type=MessageType.REPORT,
#         content="Thank you for your report. We have received your complaint and our team is currently investigating this matter. We will review all details and take appropriate action. You will be notified of any updates or resolutions.",
#         report_id=report.id,
#         role=UserType.ADMIN,
#     )
#     db.add(admin_message)
    
#     report.report_status = ReportStatus.INVESTIGATING
#     await db.commit()

#     return report


async def create_report(
    db: AsyncSession, order_id: UUID, current_user: User, report_data: ReportCreate
) -> ReportResponseSchema:
    """Create a new report with automatic admin acknowledgment message"""
    from sqlalchemy.exc import IntegrityError
    from fastapi import HTTPException
    
    try:
        # Get the order with delivery information
        order_stmt = (
            select(Order)
            .options(
                selectinload(Order.delivery),
            )
            .where(Order.id == order_id)
        )
        order_result = await db.execute(order_stmt)
        order = order_result.scalar_one_or_none()
        
        if not order:
            raise ValueError(f"Order with ID {order_id} not found")
        
        # Determine defendant_id based on reported user type
        if report_data.reported_user_type == ReportedUserType.CUSTOMER:
            defendant_id = order.owner_id
        elif report_data.reported_user_type == ReportedUserType.VENDOR:
            defendant_id = order.vendor_id
        elif report_data.reported_user_type == ReportedUserType.DISPATCH:
            if not order.delivery:
                raise ValueError("Order has no delivery information for dispatch report")
            defendant_id = order.delivery.dispatch_id
        else:
            raise ValueError(f"Invalid reported user type: {report_data.reported_user_type}")
        
        # Proactive check for existing report
        existing_report_stmt = select(UserReport).where(
            UserReport.complainant_id == current_user.id,
            UserReport.defendant_id == defendant_id,
            UserReport.order_id == order_id
        )
        
        # Add delivery_id check if it's a dispatch report
        if report_data.reported_user_type == ReportedUserType.DISPATCH and order.delivery:
            existing_report_stmt = existing_report_stmt.where(
                UserReport.delivery_id == order.delivery.id
            )
        
        existing_report_result = await db.execute(existing_report_stmt)
        existing_report = existing_report_result.scalar_one_or_none()
        
        if existing_report:
            report_target = "order" if report_data.reported_user_type != ReportedUserType.DISPATCH else "delivery"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You have already submitted a report for this {report_target} against this user. "
                       f"Report ID: {existing_report.id}. Please check your existing reports or contact support."
            )
        
        # Create the report
        report = UserReport(
            reported_user_type=report_data.reported_user_type,
            report_type=report_data.report_type,
            description=report_data.description,
            complainant_id=current_user.id,
            defendant_id=defendant_id,
            order_id=order_id,
            # delivery_id=order.delivery.id if report_data.reported_user_type == ReportedUserType.DISPATCH else None,
            report_tag=ReportTag.COMPLAINANT,
            report_status=ReportStatus.PENDING
        )
        
        db.add(report)
        await db.flush()  # Flush to get the report ID
        
        # Create admin acknowledgment message using insert
        admin_message_stmt = insert(Message).values(
            message_type=MessageType.REPORT,
            content="Thank you for your report. We have received your complaint and our team is currently investigating this matter. We will review all details and take appropriate action. You will be notified of any updates or resolutions.",
            report_id=report.id,
            role=UserType.ADMIN,
        )
        await db.execute(admin_message_stmt)
        
        # Update report status to investigating
        report.report_status = ReportStatus.INVESTIGATING
        
        # Commit all changes
        await db.commit()
        
        return report
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        await db.rollback()
        raise
        
    except IntegrityError as e:
        # Rollback on constraint violation
        await db.rollback()
        
        # Check which constraint was violated
        error_msg = str(e.orig).lower()
        
        if "uq_reporter_order_report" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You have already submitted a report for this order against this user. "
                       f"Please check your existing reports or contact support if you need to update your report."
            )
        elif "uq_reporter_delivery_report" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You have already submitted a report for this delivery against this user. "
                       f"Please check your existing reports or contact support if you need to update your report."
            )
        else:
            # Generic constraint violation
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A report with these details already exists. Please check your existing reports."
            )
            
    except ValueError as e:
        # Handle validation errors (like missing order, invalid user type, etc.)
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        
    except Exception as e:
        # Rollback on any other error
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="An error occurred while creating the report. Please try again."
        )





async def add_message_to_report(
    db: AsyncSession, report_id: UUID, current_user: User, message: MessageCreate
) -> Message:
    """Add a message to an existing report thread and mark report as unread."""
    # Add the message
    message_obj = Message(
        message_type=MessageType.REPORT,
        content=message.content,
        sender_id=current_user.id,
        report_id=report_id,
        role=current_user.user_type,
    )
    db.add(message_obj)

    # Set report.is_read = False using direct update
    await db.execute(
        update(UserReport)
        .where(UserReport.id == report_id)
        .values(is_read=False)
    )

    await db.commit()
    # Invalidate cache for both users
    report = await db.get(UserReport, report_id)
    redis_client.delete(f"user:{report.complainant_id}:report_threads")
    redis_client.delete(f"user:{current_user.id}:report_threads")
    redis_client.delete(f"user:{report.defendant_id}:report_threads")
    redis_client.delete(f"report:{report_id}:thread:{report.complainant_id}")
    redis_client.delete(f"report:{report_id}:thread:{report.defendant_id}")
    return message_obj




async def mark_message_as_read(db: AsyncSession, message_id: UUID, current_user: User):
    """Mark a message as read for a specific user"""
    # Check if read status already exists
    stmt = select(MessageReadStatus).where(
        and_(
            MessageReadStatus.message_id == message_id,
            MessageReadStatus.user_id == current_user.id,
        )
    )
    result = await db.execute(stmt)
    read_status = result.scalar_one_or_none()

    if read_status:
        read_status.read = True
        read_status.read_at = datetime.now()
    else:
        read_status = MessageReadStatus(
            message_id=message_id, user_id=current_user.id, read=True, read_at=datetime.now()
        )
        db.add(read_status)

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
            or_(UserReport.complainant_id == user_id, UserReport.defendant_id == user_id)
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
            read_status = next((rs for rs in msg.read_status if rs.user_id == user_id), None)
            is_msg_read = read_status.read if read_status else False
            thread.append(
                ThreadMessage(
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
                created_at=report.created_at,
                thread=thread,
            )
        )
    redis_client.setex(cache_key, settings.REDIS_EX, json.dumps([msg.model_dump() for msg in report_messages], default=str))
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
            db.add(MessageReadStatus(
                message_id=msg.id, user_id=user.id, read=True, read_at=datetime.datetime.now()
            ))

    # Set report.is_read = True
    await db.execute(
        update(UserReport)
        .where(UserReport.id == report_id)
        .values(is_read=True)
    )
    await db.commit()


async def delete_report_if_allowed(db: AsyncSession, report_id: UUID) -> None:
    """Delete a report and its thread if status is dismissed or resolved."""
    stmt = select(UserReport).where(UserReport.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    if report.report_status not in [ReportStatus.DISMISSED, ReportStatus.RESOLVED]:
        raise HTTPException(status_code=400, detail="Can only delete reports that are dismissed or resolved.")


    await db.delete(report)
    await db.commit()
    # Invalidate cache for both users
    redis_client.delete(f"user:{report.complainant_id}:report_threads")
    redis_client.delete(f"user:{report.defendant_id}:report_threads")
    redis_client.delete(f"report:{report_id}:thread:{report.complainant_id}")
    redis_client.delete(f"report:{report_id}:thread:{report.defendant_id}")
    return None


async def update_report_status(db: AsyncSession, report_id: UUID, new_status: ReportStatus, current_user: User)->StatusUpdate:
    """Update the status of a report. Only admin or the complainant can update."""
    stmt = select(UserReport).where(UserReport.id == report_id)
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    # Allow only admin or the complainant to update
    is_admin = getattr(current_user, 'user_type', None) == UserType.ADMIN
    if current_user.id != report.complainant_id and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to update this report.")

    report.report_status = new_status
    await db.commit()
    await db.refresh(report)
    # Invalidate cache for both users
    redis_client.delete(f"user:{report.complainant_id}:report_threads")
    redis_client.delete(f"user:{report.defendant_id}:report_threads")
    redis_client.delete(f"report:{report_id}:thread:{report.complainant_id}")
    redis_client.delete(f"report:{report_id}:thread:{report.defendant_id}")
    return StatusUpdate.model_validate(new_status)

    


async def get_report_by_id(db: AsyncSession, current_user: User, report_id: UUID) -> ReportMessage:
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    if current_user.id not in [report.complainant_id, report.defendant_id]:
        raise HTTPException(status_code=403, detail="Not authorized to view this report.")
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
        read_status = next((rs for rs in msg.read_status if rs.user_id == current_user.id), None)
        is_msg_read = read_status.read if read_status else False
        thread.append(
            ThreadMessage(
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
        created_at=report.created_at,
        thread=thread,
    )
    redis_client.setex(cache_key, settings.REDIS_EX, report_message.model_dump_json())
    return report_message


async def get_unread_badge_count(db: AsyncSession, current_user: User) -> BadgeCount:
    """Return the count of unread messages for the current user (report threads)."""
    # Get all reports where user is involved
    reports_stmt = select(UserReport.id).where(
        or_(UserReport.complainant_id == current_user.id, UserReport.defendant_id == current_user.id)
    )
    reports_result = await db.execute(reports_stmt)
    report_ids = [r[0] for r in reports_result.all()]
    if not report_ids:
        return 0

    # Get all messages in those reports
    messages_stmt = select(Message.id).where(Message.report_id.in_(report_ids))
    messages_result = await db.execute(messages_stmt)
    message_ids = [m[0] for m in messages_result.all()]
    if not message_ids:
        return 0

    # Count unread MessageReadStatus for this user
    unread_stmt = select(MessageReadStatus).where(
        MessageReadStatus.message_id.in_(message_ids),
        MessageReadStatus.user_id == current_user.id,
        MessageReadStatus.read == False
    )
    unread_result = await db.execute(unread_stmt)
    unread_count = len(unread_result.fetchall())
    return {'unread_count': unread_count}


