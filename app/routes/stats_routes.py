from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import date

from app.auth.auth import get_current_user
from app.database.database import get_db
from app.models.models import User
from app.schemas.stats_schema import (
    StatsPeriod, OrderStatsResponse, RevenueStatsResponse,
    UserStatsResponse, ComprehensiveStatsResponse,
    StatsResponseWrapper, PlatformOverview
)
from app.schemas.status_schema import UserType
from app.services.stats_service import (
    get_order_statistics, get_revenue_statistics,
    get_user_statistics, get_platform_overview,
    get_comprehensive_stats
)
from fastapi import HTTPException

router = APIRouter(prefix="/api/stats", tags=["Statistics"])


def check_admin_or_staff_access(current_user: User):
    """Check if user has admin or staff access for statistics"""
    if current_user.user_type not in [UserType.ADMIN, UserType.STAFF, UserType.MODERATOR]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin, staff, and moderator users can access statistics"
        )


@router.get(
    "/orders",
    response_model=OrderStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get order statistics",
    description="Get daily order statistics by type (package, food, laundry) for specified period."
)
async def get_order_stats(
    period: StatsPeriod = Query(..., description="Time period for statistics"),
    custom_start: Optional[date] = Query(None, description="Custom start date (required for custom period)"),
    custom_end: Optional[date] = Query(None, description="Custom end date (required for custom period)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get order statistics for the specified period.
    
    - **Requires**: Admin, Staff, or Moderator access
    - **Parameters**: 
        - period: Time period (last_7_days, last_30_days, last_3_months, custom)
        - custom_start: Start date for custom period
        - custom_end: End date for custom period
    - **Returns**: Daily order statistics by type with summary
    
    **Example Response:**
    ```json
    {
        "period": "last_7_days",
        "start_date": "2024-04-01",
        "end_date": "2024-04-07",
        "data": [
            {"date": "2024-04-01", "package": 222, "food": 150, "laundry": 100, "total": 472},
            {"date": "2024-04-02", "package": 180, "food": 200, "laundry": 80, "total": 460}
        ],
        "summary": {
            "total_orders": 932,
            "package_orders": 402,
            "food_orders": 350,
            "laundry_orders": 180
        }
    }
    ```
    """
    check_admin_or_staff_access(current_user)
    return await get_order_statistics(db, period, custom_start, custom_end)


@router.get(
    "/revenue",
    response_model=RevenueStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get revenue statistics",
    description="Get daily revenue statistics from orders and deliveries for specified period."
)
async def get_revenue_stats(
    period: StatsPeriod = Query(..., description="Time period for statistics"),
    custom_start: Optional[date] = Query(None, description="Custom start date (required for custom period)"),
    custom_end: Optional[date] = Query(None, description="Custom end date (required for custom period)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get revenue statistics for the specified period.
    
    - **Requires**: Admin, Staff, or Moderator access
    - **Returns**: Daily revenue breakdown by source (orders, deliveries)
    """
    check_admin_or_staff_access(current_user)
    return await get_revenue_statistics(db, period, custom_start, custom_end)


@router.get(
    "/users",
    response_model=UserStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get user statistics",
    description="Get daily user statistics including new registrations and active users."
)
async def get_user_stats(
    period: StatsPeriod = Query(..., description="Time period for statistics"),
    custom_start: Optional[date] = Query(None, description="Custom start date (required for custom period)"),
    custom_end: Optional[date] = Query(None, description="Custom end date (required for custom period)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get user statistics for the specified period.
    
    - **Requires**: Admin, Staff, or Moderator access
    - **Returns**: Daily user registration and activity statistics
    """
    check_admin_or_staff_access(current_user)
    return await get_user_statistics(db, period, custom_start, custom_end)


@router.get(
    "/overview",
    response_model=PlatformOverview,
    status_code=status.HTTP_200_OK,
    summary="Get platform overview",
    description="Get high-level platform statistics and key performance indicators."
)
async def get_platform_stats_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get overall platform statistics overview.
    
    - **Requires**: Admin, Staff, or Moderator access
    - **Returns**: High-level platform metrics including user counts, order counts, revenue, etc.
    
    **Example Response:**
    ```json
    {
        "total_users": 15420,
        "total_vendors": 1250,
        "total_customers": 13500,
        "total_riders": 670,
        "total_orders": 45600,
        "total_revenue": "2450000.00",
        "pending_orders": 125,
        "completed_orders": 42100,
        "active_users_today": 890,
        "orders_today": 234
    }
    ```
    """
    check_admin_or_staff_access(current_user)
    return await get_platform_overview(db)


@router.get(
    "/comprehensive",
    response_model=ComprehensiveStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get comprehensive statistics",
    description="Get all platform statistics in one comprehensive response."
)
async def get_comprehensive_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get comprehensive platform statistics.
    
    - **Requires**: Admin, Staff, or Moderator access
    - **Returns**: All platform statistics including:
        - Platform overview
        - Order status distribution
        - Payment method distribution
        - Delivery statistics
        - Wallet and transaction statistics
        - Top vendors and customers
    
    **Use Case**: Perfect for admin dashboard that needs to display multiple statistics at once.
    """
    check_admin_or_staff_access(current_user)
    return await get_comprehensive_stats(db)


@router.get(
    "/dashboard",
    response_model=StatsResponseWrapper,
    status_code=status.HTTP_200_OK,
    summary="Get dashboard statistics",
    description="Get curated statistics for admin dashboard view."
)
async def get_dashboard_stats(
    period: StatsPeriod = Query(StatsPeriod.LAST_7_DAYS, description="Period for time-based stats"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get curated statistics for dashboard view.
    
    - **Requires**: Admin, Staff, or Moderator access
    - **Returns**: Combined statistics optimized for dashboard display
    """
    check_admin_or_staff_access(current_user)
    
    try:
        # Get multiple statistics for dashboard
        overview = await get_platform_overview(db)
        order_stats = await get_order_statistics(db, period)
        revenue_stats = await get_revenue_statistics(db, period)
        
        dashboard_data = {
            "overview": overview.model_dump(),
            "orders": {
                "period_data": order_stats.data,
                "summary": order_stats.summary
            },
            "revenue": {
                "period_data": revenue_stats.data,
                "summary": revenue_stats.summary
            },
            "period": period
        }
        
        return StatsResponseWrapper(
            success=True,
            message="Dashboard statistics retrieved successfully",
            data=dashboard_data
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve dashboard statistics: {str(e)}"
        )


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Statistics service health check",
    description="Check if the statistics service is working properly."
)
async def stats_health_check():
    """
    Health check endpoint for the statistics service.
    """
    return {
        "status": "healthy",
        "service": "statistics",
        "message": "Statistics service is operational",
        "endpoints": [
            "/api/stats/orders",
            "/api/stats/revenue", 
            "/api/stats/users",
            "/api/stats/overview",
            "/api/stats/comprehensive",
            "/api/stats/dashboard"
        ]
    }


# Additional utility endpoints

@router.get(
    "/periods",
    status_code=status.HTTP_200_OK,
    summary="Get available time periods",
    description="Get list of available time periods for statistics."
)
async def get_available_periods():
    """
    Get available time periods for statistics.
    
    - **Returns**: List of available periods and their descriptions
    """
    return {
        "periods": [
            {
                "value": StatsPeriod.LAST_7_DAYS,
                "label": "Last 7 Days",
                "description": "Statistics for the last 7 days including today"
            },
            {
                "value": StatsPeriod.LAST_30_DAYS,
                "label": "Last 30 Days", 
                "description": "Statistics for the last 30 days including today"
            },
            {
                "value": StatsPeriod.LAST_3_MONTHS,
                "label": "Last 3 Months",
                "description": "Statistics for approximately the last 3 months"
            },
            {
                "value": StatsPeriod.CUSTOM,
                "label": "Custom Range",
                "description": "Custom date range (requires start and end dates)"
            }
        ]
    }


@router.get(
    "/quick-stats",
    status_code=status.HTTP_200_OK,
    summary="Get quick statistics",
    description="Get essential statistics for quick overview."
)
async def get_quick_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get quick essential statistics.
    
    - **Requires**: Admin, Staff, or Moderator access
    - **Returns**: Essential metrics for quick overview
    """
    check_admin_or_staff_access(current_user)
    
    try:
        overview = await get_platform_overview(db)
        
        # Quick stats focusing on key metrics
        quick_stats = {
            "users": {
                "total": overview.total_users,
                "active_today": overview.active_users_today,
                "vendors": overview.total_vendors,
                "customers": overview.total_customers
            },
            "orders": {
                "total": overview.total_orders,
                "today": overview.orders_today,
                "pending": overview.pending_orders,
                "completed": overview.completed_orders
            },
            "revenue": {
                "total": str(overview.total_revenue),
                "completion_rate": round((overview.completed_orders / overview.total_orders * 100), 2) if overview.total_orders > 0 else 0
            }
        }
        
        return StatsResponseWrapper(
            success=True,
            message="Quick statistics retrieved successfully",
            data=quick_stats
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve quick statistics: {str(e)}"
        )
