import json

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select, and_, update
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status,BackgroundTasks

from app.schemas.review_schema import (ReviewCreate, 
        ReviewResponse, 
        ReviewerType,
        VendorReviewResponse,  
        ReportingType,
IssueStatus, ReportIssueCreate, ReportIssueUpdate, ReportIssueResponse)
from app.models.models import User, Review, Delivery, Order, ReportIssue, Profile, ProfileImage, OrderItem
from app.schemas.status_schema import DeliveryStatus, OrderStatus
from app.utils.utils import refresh_vendor_review_stats_view
from app.config.config import redis_client, settings
from app.utils.model_converter import model_to_response


from sqlalchemy.exc import NoResultFound
from uuid import UUID



def convert_report_to_response(report: ReportIssue) -> ReportIssueResponse:
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
        updated_at=report.updated_at
    )



async def create_review(
    db: AsyncSession,
    current_user: User,
    data: ReviewCreate
) -> ReviewResponse:

    # Shared variables
    reviewer_id = current_user.id
    reviewee_id = data.reviewee_id

    order_result = await db.execute(select(Order).where(Order.id==data.order_id)) 
    order = order_result.scalar_one_or_none()

    # if data.review_type == ReviewerType.ORDER:
    if order.order.order_type == ReviewerType.ORDER:
        # Optional caching
        # cache_key = f"review:order:{data.order_id}:user:{reviewer_id}"
        # if redis_client.get(cache_key):
        #     print('FROM CACHE')
        #     raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Review already exists for this order.")

        # Check if review already exists
        existing_review = await db.execute(
            select(Review).where(
                Review.order_id == data.order_id,
                Review.reviewer_id == reviewer_id
            )
        )
        if existing_review.scalar():
        #     redis_client.setex(cache_key, True, settings.REDIS_EX)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Review already exists for this order.")

        # Order validation
        order = await db.get(Order, data.order_id)
        if not order or order.order_status != OrderStatus.RECEIVED:
            raise HTTPException(status_code=400, detail="Order is not completed or doesn't exist.")

        review = Review(
            order_id=data.order_id,
            reviewer_id=reviewer_id,
            reviewee_id=reviewee_id,
            rating=data.rating,
            comment=data.comment,
            review_type=ReviewerType.ORDER
        )

    elif order.order.order_type == ReviewerType.PRODUCT:
        # cache_key = f"review:delivery:{data.item_id}:user:{reviewer_id}"
        # if redis_client.get(cache_key):
        #     raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Review already exists for this delivery.")

        existing_review = await db.execute(
            select(Review).where(
                Review.item_id == data.item_id,
                Review.reviewer_id == reviewer_id
            )
        )
        if existing_review.scalar():
            # redis_client.setex(cache_key, settings.REDIS_EX)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Review already exists for this delivery.")

        delivery = await db.get(Delivery, data.delivery_id)
        if not delivery or delivery.delivery_status != DeliveryStatus.RECEIVED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Delivery is not completed or doesn't exist.")

        review = Review(
            item_id=data.item_id,
            reviewer_id=reviewer_id,
            reviewee_id=reviewee_id,
            rating=data.rating,
            comment=data.comment,
            review_type=ReviewerType.PRODUCT
        )

    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid review type.")

    # Save review
    db.add(review)
    await db.commit()
    await db.refresh(review)

    return review


async def fetch_vendor_reviews(
    vendor_id: UUID,
    db: AsyncSession,
    current_user:User
) -> list[VendorReviewResponse]:

    cache_key = f"reviews:{vendor_id}"
    cached_reviews = redis_client.get(cache_key)

    if cached_reviews:
        return [VendorReviewResponse(**r) for r in cached_reviews]

    # DB fallback
    stmt = select(Review).options(
        selectinload(Review.reviewer).selectinload(User.profile).selectinload(Profile.profile_image)
    ).where(Review.reviewee_id == vendor_id)


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
                full_name=r.reviewer.profile.full_name if r.reviewer.profile and r.reviewer.profile.full_name
                else r.reviewer.profile.business_name if r.reviewer.profile else None,
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
        await redis_client.setex(cache_key, [r.dict() for r in response_list], default=str)

    return response_list

async def create_report(db: AsyncSession, report_data: ReportIssueCreate, current_user: User) -> ReportIssueResponse:
    """Create a new report based on order_id or delivery_id"""
    
    try:
        # Validate that either order_id or delivery_id is provided
        if not report_data.order_id and not report_data.delivery_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either order_id or delivery_id must be provided"
            )
        
        if report_data.order_id and report_data.delivery_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot provide both order_id and delivery_id"
            )
        
        # Validate that the reported user ID matches the reporting type
        reported_user_id = None
        if report_data.reporting == ReportingType.VENDOR:
            if not report_data.vendor_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="vendor_id is required when reporting a vendor"
                )
            reported_user_id = report_data.vendor_id
        
        elif report_data.reporting == ReportingType.CUSTOMER:
            if not report_data.customer_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="customer_id is required when reporting a customer"
                )
            reported_user_id = report_data.customer_id
        
        elif report_data.reporting == ReportingType.DISPATCH:
            if not report_data.dispatch_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="dispatch_id is required when reporting a dispatch"
                )
            reported_user_id = report_data.dispatch_id
        
        # Check if reporter is trying to report themselves
        if reported_user_id == current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot report yourself"
            )
        
        # Check for existing report (based on unique constraints)
        existing_report = None
        
        if report_data.order_id and report_data.vendor_id:
            stmt = select(ReportIssue).where(
                and_(
                    ReportIssue.reporter_id == current_user.id,
                    ReportIssue.order_id == report_data.order_id,
                    ReportIssue.vendor_id == report_data.vendor_id
                )
            )
            result = await db.execute(stmt)
            existing_report = result.scalar_one_or_none()
        
        elif report_data.delivery_id and report_data.dispatch_id:
            stmt = select(ReportIssue).where(
                and_(
                    ReportIssue.reporter_id == current_user.id,
                    ReportIssue.delivery_id == report_data.delivery_id,
                    ReportIssue.dispatch_id == report_data.dispatch_id
                )
            )
            result = await db.execute(stmt)
            existing_report = result.scalar_one_or_none()
        
        if existing_report:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already reported this issue"
            )
        
        # Create the new report
        new_report = ReportIssue(
            order_id=report_data.order_id,
            delivery_id=report_data.delivery_id,
            dispatch_id=report_data.dispatch_id,
            vendor_id=report_data.vendor_id,
            customer_id=report_data.customer_id,
            reporter_id=current_user.id,
            issue_status=IssueStatus.PENDING,
            description=report_data.description,
            issue_type=report_data.issue_type,
            reporting=report_data.reporting
        )
        
        db.add(new_report)
        await db.commit()
        await db.refresh(new_report)
        
        # Clear Redis cache (ensure redis_client is properly initialized)
        
        redis_client.delete(f'user_report:{current_user.id}')
        
        return new_report
    
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Rollback the transaction on any other error
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while creating the report: {str(e)}"
        )

async def get_reports_by_user(
    db: AsyncSession,
    current_user: User,
    background_task: BackgroundTasks | None = None,
) -> list[ReportIssueResponse]:
    """
    Fetch all reports involving a user (as reported user or reporter) with Redis caching.
    
    Args:
        db: Database session
        redis: Redis client
        current_user: The user whose reports to fetch
        background_tasks: Optional FastAPI background tasks
        force_refresh: If True, bypass cache and force refresh from database
        
    Returns:
        List of ReportIssueResponse objects
    """
    cache_key = f'user_reports:{current_user.id}'
    
    # Try cache first (unless force_refresh is True)
    cached_reports = redis_client.get(cache_key)
    if cached_reports:
        cached_data = json.loads(cached_reports)
        return [ReportIssueResponse.model_validate(report) for report in cached_data]


    # Build query
    user_filters = or_(
        ReportIssue.vendor_id == current_user.id,
        ReportIssue.customer_id == current_user.id,
        ReportIssue.dispatch_id == current_user.id,
        ReportIssue.reporter_id == current_user.id
    )
    query = select(ReportIssue).where(user_filters).order_by(ReportIssue.created_at.desc())
    
    # Execute query
    result = await db.execute(query)
    reports = result.scalars().all()
    
    # Convert to response models
    report_responses = [convert_report_to_response(report) for report in reports]
    
    # Prepare data for caching
    reports_data = [report.model_dump() for report in report_responses]
    
    # Cache the results (either in background or directly)
    background_tasks.add_task(
        redis_client.setex,
        cache_key,
        settings.REDIS_EX,
        reports_json
    )

      
    return report_responses


async def get_report_by_id(
    db: AsyncSession, 
    current_user: User, 
    report_id: UUID,
    background_task: BackgroundTasks | None = None
) -> ReportIssueResponse | None:
    """
    Get a specific report by ID with Redis caching.
    
    Args:
        db: Database session
        current_user: The authenticated user
        report_id: ID of the report to fetch
        background_tasks: Optional FastAPI background tasks
        
    Returns:
        ReportIssueResponse if found and accessible by user, None otherwise
    """
    cache_key = f'report:{report_id}'
    
    # Try to get from cache first
    cached_report = redis_client.get(cache_key)
    if cached_report:
        return ReportIssueResponse.model_validate_json(cached_report)

    # Build query with user access filters
    user_filters = or_(
        ReportIssue.vendor_id == current_user.id,
        ReportIssue.customer_id == current_user.id,
        ReportIssue.dispatch_id == current_user.id,
        ReportIssue.reporter_id == current_user.id
    )
    stmt = select(ReportIssue).where(
        ReportIssue.id == report_id,
        user_filters
    )
    
    # Execute query
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()
    
    if not report:
        return None
    
    # Convert to response model
    report_response = convert_report_to_response(report)
    
    # Cache the response (as JSON string)
    report_json = report_response.model_dump_json()
    
    background_tasks.add_task(
        redis_client.setex,
        cache_key,
        settings.REDIS_EX,
        report_json
    )

    
    return report_response

async def update_report_status(
    db: AsyncSession,
    report_id: UUID, 
    update_data: ReportIssueUpdate,
    current_user: User
) -> ReportIssueUpdate:
    """Update report status (typically by admin or involved parties)"""

    cache_key = f'report_id:{report_id}'
    
    # report = await get_report_by_id(db, report_id)
    report_result = await db.execute(select(ReportIssue).where(ReportIssue.id==report_id, ReportIssue.reporter_id==current_user.id))

    report = report_result.scalar_one_or_none()

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found"
        )
    await db.execute(update(ReportIssue).where(ReportIssue.id==report_id).values(issue_status=update_data.issue_status).returning(ReportIssue.issue_status))    

    await db.commit()
    await db.refresh(report)

    redis_client.delete(cache_key)
    
    return {'issue_status': report.issue_status}

