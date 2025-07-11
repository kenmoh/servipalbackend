from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy import select, func, and_, or_, cast, Date, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import json
import logging

from app.models.models import (
    User, Order, Transaction, Wallet, Review, Delivery, 
    Profile, OrderItem, Item
)
from app.schemas.stats_schema import (
    StatsPeriod, OrderStatsDaily, OrderStatsResponse,
    RevenueStatsDaily, RevenueStatsResponse,
    UserStatsDaily, UserStatsResponse,
    TopVendorStat, TopCustomerStat, PlatformOverview,
    OrderStatusStats, PaymentMethodStats, DeliveryStats,
    WalletStats, ComprehensiveStatsResponse,
    StatsResponseWrapper
)
from app.schemas.order_schema import OrderType
from app.schemas.status_schema import (
    OrderStatus, PaymentMethod, PaymentStatus, 
    TransactionType, UserType, DeliveryStatus
)
from app.schemas.review_schema import ReviewType
from app.utils.logger_config import setup_logger
from app.config.config import redis_client, settings

logger = setup_logger()


def get_date_range(period: StatsPeriod, custom_start: Optional[date] = None, custom_end: Optional[date] = None) -> tuple[date, date]:
    """
    Get start and end dates for the specified period.
    
    Args:
        period: The time period enum
        custom_start: Custom start date (for CUSTOM period)
        custom_end: Custom end date (for CUSTOM period)
        
    Returns:
        Tuple of (start_date, end_date)
    """
    today = date.today()
    
    if period == StatsPeriod.LAST_7_DAYS:
        start_date = today - timedelta(days=6)  # Last 7 days including today
        end_date = today
    elif period == StatsPeriod.LAST_30_DAYS:
        start_date = today - timedelta(days=29)  # Last 30 days including today
        end_date = today
    elif period == StatsPeriod.LAST_3_MONTHS:
        start_date = today - timedelta(days=89)  # Approximately 3 months
        end_date = today
    elif period == StatsPeriod.CUSTOM:
        if not custom_start or not custom_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom start and end dates are required for custom period"
            )
        start_date = custom_start
        end_date = custom_end
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid period specified"
        )
    
    return start_date, end_date


async def get_order_statistics(
    db: AsyncSession,
    period: StatsPeriod,
    custom_start: Optional[date] = None,
    custom_end: Optional[date] = None
) -> OrderStatsResponse:
    """
    Get order statistics for the specified period.
    
    Args:
        db: Database session
        period: Time period for statistics
        custom_start: Custom start date (optional)
        custom_end: Custom end date (optional)
        
    Returns:
        OrderStatsResponse with daily order statistics
    """
    try:
        start_date, end_date = get_date_range(period, custom_start, custom_end)
        
        # Check cache
        cache_key = f"order_stats:{period}:{start_date}:{end_date}"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            return OrderStatsResponse(**data)
        
        # Query order statistics grouped by date and order type
        stmt = (
            select(
                cast(Order.created_at, Date).label('order_date'),
                Order.order_type,
                func.count(Order.id).label('count')
            )
            .where(
                and_(
                    cast(Order.created_at, Date) >= start_date,
                    cast(Order.created_at, Date) <= end_date
                )
            )
            .group_by(cast(Order.created_at, Date), Order.order_type)
            .order_by(cast(Order.created_at, Date))
        )
        
        result = await db.execute(stmt)
        raw_data = result.all()
        
        # Process data into daily statistics
        daily_stats: Dict[date, OrderStatsDaily] = {}
        
        # Initialize all dates in range with zero counts
        current_date = start_date
        while current_date <= end_date:
            daily_stats[current_date] = OrderStatsDaily(
                date=current_date,
                package=0,
                food=0,
                laundry=0,
                total=0
            )
            current_date += timedelta(days=1)
        
        # Fill in actual data
        for order_date, order_type, count in raw_data:
            if order_date in daily_stats:
                if order_type == OrderType.PACKAGE:
                    daily_stats[order_date].package = count
                elif order_type == OrderType.FOOD:
                    daily_stats[order_date].food = count
                elif order_type == OrderType.LAUNDRY:
                    daily_stats[order_date].laundry = count
                
                daily_stats[order_date].total += count
        
        # Convert to list and calculate summary
        data = list(daily_stats.values())
        summary = {
            "total_orders": sum(day.total for day in data),
            "package_orders": sum(day.package for day in data),
            "food_orders": sum(day.food for day in data),
            "laundry_orders": sum(day.laundry for day in data)
        }
        
        response = OrderStatsResponse(
            period=period,
            start_date=start_date,
            end_date=end_date,
            data=data,
            summary=summary
        )
        
        # Cache for 15 minutes
        redis_client.setex(cache_key, 900, response.model_dump_json())
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting order statistics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve order statistics"
        )


async def get_revenue_statistics(
    db: AsyncSession,
    period: StatsPeriod,
    custom_start: Optional[date] = None,
    custom_end: Optional[date] = None
) -> RevenueStatsResponse:
    """
    Get revenue statistics for the specified period.
    """
    try:
        start_date, end_date = get_date_range(period, custom_start, custom_end)
        
        # Check cache
        cache_key = f"revenue_stats:{period}:{start_date}:{end_date}"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            return RevenueStatsResponse(**data)
        
        # Query revenue from orders
        order_revenue_stmt = (
            select(
                cast(Order.created_at, Date).label('order_date'),
                func.sum(Order.total_price).label('order_revenue'),
                func.count(Order.id).label('order_count')
            )
            .where(
                and_(
                    cast(Order.created_at, Date) >= start_date,
                    cast(Order.created_at, Date) <= end_date,
                    Order.order_payment_status == PaymentStatus.PAID
                )
            )
            .group_by(cast(Order.created_at, Date))
        )
        
        # Query delivery revenue
        delivery_revenue_stmt = (
            select(
                cast(Delivery.created_at, Date).label('delivery_date'),
                func.sum(Delivery.delivery_fee).label('delivery_revenue')
            )
            .where(
                and_(
                    cast(Delivery.created_at, Date) >= start_date,
                    cast(Delivery.created_at, Date) <= end_date,
                    Delivery.delivery_status == DeliveryStatus.RECEIVED
                )
            )
            .group_by(cast(Delivery.created_at, Date))
        )
        
        order_result = await db.execute(order_revenue_stmt)
        delivery_result = await db.execute(delivery_revenue_stmt)
        
        order_data = {row.order_date: (row.order_revenue or Decimal('0'), row.order_count) 
                     for row in order_result.all()}
        delivery_data = {row.delivery_date: row.delivery_revenue or Decimal('0') 
                        for row in delivery_result.all()}
        
        # Process data into daily statistics
        daily_stats: Dict[date, RevenueStatsDaily] = {}
        
        # Initialize all dates in range
        current_date = start_date
        while current_date <= end_date:
            order_rev, order_count = order_data.get(current_date, (Decimal('0'), 0))
            delivery_rev = delivery_data.get(current_date, Decimal('0'))
            
            daily_stats[current_date] = RevenueStatsDaily(
                date=current_date,
                order_revenue=order_rev,
                delivery_revenue=delivery_rev,
                total_revenue=order_rev + delivery_rev,
                transaction_count=order_count
            )
            current_date += timedelta(days=1)
        
        # Convert to list and calculate summary
        data = list(daily_stats.values())
        summary = {
            "total_revenue": str(sum(day.total_revenue for day in data)),
            "order_revenue": str(sum(day.order_revenue for day in data)),
            "delivery_revenue": str(sum(day.delivery_revenue for day in data)),
            "total_transactions": sum(day.transaction_count for day in data)
        }
        
        response = RevenueStatsResponse(
            period=period,
            start_date=start_date,
            end_date=end_date,
            data=data,
            summary=summary
        )
        
        # Cache for 15 minutes
        redis_client.setex(cache_key, 900, response.model_dump_json())
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting revenue statistics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve revenue statistics"
        )


async def get_user_statistics(
    db: AsyncSession,
    period: StatsPeriod,
    custom_start: Optional[date] = None,
    custom_end: Optional[date] = None
) -> UserStatsResponse:
    """
    Get user statistics for the specified period.
    """
    try:
        start_date, end_date = get_date_range(period, custom_start, custom_end)
        
        # Check cache
        cache_key = f"user_stats:{period}:{start_date}:{end_date}"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            return UserStatsResponse(**data)
        
        # Query new user registrations by date
        new_users_stmt = (
            select(
                cast(User.created_at, Date).label('reg_date'),
                func.count(User.id).label('new_users')
            )
            .where(
                and_(
                    cast(User.created_at, Date) >= start_date,
                    cast(User.created_at, Date) <= end_date
                )
            )
            .group_by(cast(User.created_at, Date))
        )
        
        # Query active users (users who placed orders) by date
        active_users_stmt = (
            select(
                cast(Order.created_at, Date).label('order_date'),
                func.count(func.distinct(Order.owner_id)).label('active_users')
            )
            .where(
                and_(
                    cast(Order.created_at, Date) >= start_date,
                    cast(Order.created_at, Date) <= end_date
                )
            )
            .group_by(cast(Order.created_at, Date))
        )
        
        new_users_result = await db.execute(new_users_stmt)
        active_users_result = await db.execute(active_users_stmt)
        
        new_users_data = {row.reg_date: row.new_users for row in new_users_result.all()}
        active_users_data = {row.order_date: row.active_users for row in active_users_result.all()}
        
        # Get total users at start date
        total_users_stmt = select(func.count(User.id)).where(User.created_at < start_date)
        total_users_result = await db.execute(total_users_stmt)
        running_total = total_users_result.scalar() or 0
        
        # Process data into daily statistics
        daily_stats: Dict[date, UserStatsDaily] = {}
        
        current_date = start_date
        while current_date <= end_date:
            new_users = new_users_data.get(current_date, 0)
            active_users = active_users_data.get(current_date, 0)
            running_total += new_users
            
            daily_stats[current_date] = UserStatsDaily(
                date=current_date,
                new_users=new_users,
                active_users=active_users,
                total_users=running_total
            )
            current_date += timedelta(days=1)
        
        # Convert to list and calculate summary
        data = list(daily_stats.values())
        summary = {
            "total_new_users": sum(day.new_users for day in data),
            "peak_active_users": max((day.active_users for day in data), default=0),
            "final_total_users": data[-1].total_users if data else 0
        }
        
        response = UserStatsResponse(
            period=period,
            start_date=start_date,
            end_date=end_date,
            data=data,
            summary=summary
        )
        
        # Cache for 15 minutes
        redis_client.setex(cache_key, 900, response.model_dump_json())
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user statistics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user statistics"
        )


async def get_platform_overview(db: AsyncSession) -> PlatformOverview:
    """
    Get overall platform statistics overview.
    """
    try:
        # Check cache
        cache_key = "platform_overview"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            return PlatformOverview(**data)
        
        today = date.today()
        
        # Total users by type
        total_users_stmt = select(func.count(User.id))
        total_vendors_stmt = select(func.count(User.id)).where(
            User.user_type.in_([UserType.RESTAURANT_VENDOR, UserType.LAUNDRY_VENDOR])
        )
        total_customers_stmt = select(func.count(User.id)).where(User.user_type == UserType.CUSTOMER)
        total_riders_stmt = select(func.count(User.id)).where(User.user_type == UserType.RIDER)
        
        # Order statistics
        total_orders_stmt = select(func.count(Order.id))
        pending_orders_stmt = select(func.count(Order.id)).where(
            Order.order_status.in_([OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PREPARING])
        )
        completed_orders_stmt = select(func.count(Order.id)).where(Order.order_status == OrderStatus.DELIVERED)
        orders_today_stmt = select(func.count(Order.id)).where(cast(Order.created_at, Date) == today)
        
        # Revenue
        total_revenue_stmt = select(func.sum(Order.total_price)).where(Order.order_payment_status == PaymentStatus.PAID)
        
        # Active users today
        active_users_today_stmt = select(func.count(func.distinct(Order.owner_id))).where(
            cast(Order.created_at, Date) == today
        )
        
        # Execute all queries
        results = await db.execute(total_users_stmt)
        total_users = results.scalar() or 0
        
        results = await db.execute(total_vendors_stmt)
        total_vendors = results.scalar() or 0
        
        results = await db.execute(total_customers_stmt)
        total_customers = results.scalar() or 0
        
        results = await db.execute(total_riders_stmt)
        total_riders = results.scalar() or 0
        
        results = await db.execute(total_orders_stmt)
        total_orders = results.scalar() or 0
        
        results = await db.execute(pending_orders_stmt)
        pending_orders = results.scalar() or 0
        
        results = await db.execute(completed_orders_stmt)
        completed_orders = results.scalar() or 0
        
        results = await db.execute(orders_today_stmt)
        orders_today = results.scalar() or 0
        
        results = await db.execute(total_revenue_stmt)
        total_revenue = results.scalar() or Decimal('0')
        
        results = await db.execute(active_users_today_stmt)
        active_users_today = results.scalar() or 0
        
        overview = PlatformOverview(
            total_users=total_users,
            total_vendors=total_vendors,
            total_customers=total_customers,
            total_riders=total_riders,
            total_orders=total_orders,
            total_revenue=total_revenue,
            pending_orders=pending_orders,
            completed_orders=completed_orders,
            active_users_today=active_users_today,
            orders_today=orders_today
        )
        
        # Cache for 5 minutes
        redis_client.setex(cache_key, 300, overview.model_dump_json())
        
        return overview
        
    except Exception as e:
        logger.error(f"Error getting platform overview: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve platform overview"
        )


async def get_top_vendors(db: AsyncSession, limit: int = 10) -> List[TopVendorStat]:
    """
    Get top performing vendors by order count and revenue.
    """
    try:
        # Check cache
        cache_key = f"top_vendors:{limit}"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            return [TopVendorStat(**vendor) for vendor in data]
        
        # Query top vendors by order count and revenue
        stmt = (
            select(
                User.id.label('vendor_id'),
                Profile.business_name,
                User.email,
                func.count(Order.id).label('total_orders'),
                func.sum(Order.total_price).label('total_revenue'),
                func.avg(Review.rating).label('average_rating'),
                func.count(Review.id).label('review_count')
            )
            .select_from(User)
            .join(Order, Order.vendor_id == User.id)
            .join(Profile, Profile.user_id == User.id)
            .outerjoin(Review, and_(
                Review.order_id == Order.id,
                Review.review_type == ReviewType.ORDER
            ))
            .where(
                and_(
                    User.user_type.in_([UserType.RESTAURANT_VENDOR, UserType.LAUNDRY_VENDOR]),
                    Order.order_payment_status == PaymentStatus.PAID
                )
            )
            .group_by(User.id, Profile.business_name, User.email)
            .order_by(desc(func.count(Order.id)), desc(func.sum(Order.total_price)))
            .limit(limit)
        )
        
        result = await db.execute(stmt)
        vendors = result.all()
        
        top_vendors = [
            TopVendorStat(
                vendor_id=str(vendor.vendor_id),
                vendor_name=vendor.business_name or "Unknown",
                vendor_email=vendor.email,
                total_orders=vendor.total_orders,
                total_revenue=vendor.total_revenue or Decimal('0'),
                average_rating=float(vendor.average_rating) if vendor.average_rating else None,
                review_count=vendor.review_count or 0
            )
            for vendor in vendors
        ]
        
        # Cache for 30 minutes
        redis_client.setex(cache_key, 1800, json.dumps([v.model_dump() for v in top_vendors], default=str))
        
        return top_vendors
        
    except Exception as e:
        logger.error(f"Error getting top vendors: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve top vendors"
        )


async def get_comprehensive_stats(db: AsyncSession) -> ComprehensiveStatsResponse:
    """
    Get comprehensive platform statistics.
    """
    try:
        # Check cache
        cache_key = "comprehensive_stats"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            return ComprehensiveStatsResponse(**data)
        
        # Get all individual stats
        platform_overview = await get_platform_overview(db)
        top_vendors = await get_top_vendors(db, limit=5)
        
        # Order status distribution
        order_status_stmt = (
            select(
                Order.order_status,
                func.count(Order.id).label('count')
            )
            .group_by(Order.order_status)
        )
        order_status_result = await db.execute(order_status_stmt)
        order_status_data = {row.order_status: row.count for row in order_status_result.all()}
        
        order_status_stats = OrderStatusStats(
            pending=order_status_data.get(OrderStatus.PENDING, 0),
            confirmed=order_status_data.get(OrderStatus.CONFIRMED, 0),
            preparing=order_status_data.get(OrderStatus.PREPARING, 0),
            ready=order_status_data.get(OrderStatus.READY, 0),
            picked_up=order_status_data.get(OrderStatus.PICKED_UP, 0),
            delivered=order_status_data.get(OrderStatus.DELIVERED, 0),
            cancelled=order_status_data.get(OrderStatus.CANCELLED, 0),
            total=sum(order_status_data.values())
        )
        
        # Payment method distribution
        payment_method_stmt = (
            select(
                Order.payment_method,
                func.count(Order.id).label('count')
            )
            .where(Order.order_payment_status == PaymentStatus.PAID)
            .group_by(Order.payment_method)
        )
        payment_result = await db.execute(payment_method_stmt)
        payment_data = {row.payment_method: row.count for row in payment_result.all()}
        
        payment_stats = PaymentMethodStats(
            wallet=payment_data.get(PaymentMethod.WALLET, 0),
            card=payment_data.get(PaymentMethod.CARD, 0),
            bank_transfer=payment_data.get(PaymentMethod.BANK_TRANSFER, 0),
            total=sum(payment_data.values())
        )
        
        # Delivery stats
        delivery_stats_stmt = (
            select(
                func.count(Delivery.id).label('total_deliveries'),
                func.count(Delivery.id).filter(Delivery.delivery_status != DeliveryStatus.RECEIVED).label('pending'),
                func.count(Delivery.id).filter(Delivery.delivery_status == DeliveryStatus.RECEIVED).label('completed'),
                func.sum(Delivery.delivery_fee).label('total_revenue')
            )
        )
        delivery_result = await db.execute(delivery_stats_stmt)
        delivery_row = delivery_result.first()
        
        delivery_stats = DeliveryStats(
            total_deliveries=delivery_row.total_deliveries or 0,
            pending_deliveries=delivery_row.pending or 0,
            completed_deliveries=delivery_row.completed or 0,
            average_delivery_time=None,  # Would need to calculate from delivery times
            total_delivery_revenue=delivery_row.total_revenue or Decimal('0')
        )
        
        # Wallet stats
        wallet_stats_stmt = (
            select(
                func.count(Wallet.id).label('total_wallets'),
                func.sum(Wallet.balance).label('total_balance'),
                func.sum(Wallet.escrow_balance).label('total_escrow')
            )
        )
        wallet_result = await db.execute(wallet_stats_stmt)
        wallet_row = wallet_result.first()
        
        transaction_stats_stmt = (
            select(
                func.count(Transaction.id).label('total_transactions'),
                func.count(Transaction.id).filter(Transaction.transaction_type == TransactionType.CREDIT).label('topups'),
                func.count(Transaction.id).filter(Transaction.transaction_type == TransactionType.DEBIT).label('withdrawals'),
                func.sum(Transaction.amount).filter(Transaction.transaction_type == TransactionType.CREDIT).label('topup_volume'),
                func.sum(Transaction.amount).filter(Transaction.transaction_type == TransactionType.DEBIT).label('withdrawal_volume')
            )
        )
        transaction_result = await db.execute(transaction_stats_stmt)
        transaction_row = transaction_result.first()
        
        wallet_stats = WalletStats(
            total_wallets=wallet_row.total_wallets or 0,
            total_wallet_balance=wallet_row.total_balance or Decimal('0'),
            total_escrow_balance=wallet_row.total_escrow or Decimal('0'),
            total_transactions=transaction_row.total_transactions or 0,
            total_topups=transaction_row.topups or 0,
            total_withdrawals=transaction_row.withdrawals or 0,
            topup_volume=transaction_row.topup_volume or Decimal('0'),
            withdrawal_volume=transaction_row.withdrawal_volume or Decimal('0')
        )
        
        # Top customers (simplified)
        top_customers_stmt = (
            select(
                User.id.label('customer_id'),
                Profile.full_name,
                User.email,
                func.count(Order.id).label('total_orders'),
                func.sum(Order.total_price).label('total_spent')
            )
            .select_from(User)
            .join(Order, Order.owner_id == User.id)
            .join(Profile, Profile.user_id == User.id)
            .where(
                and_(
                    User.user_type == UserType.CUSTOMER,
                    Order.order_payment_status == PaymentStatus.PAID
                )
            )
            .group_by(User.id, Profile.full_name, User.email)
            .order_by(desc(func.sum(Order.total_price)))
            .limit(5)
        )
        
        customers_result = await db.execute(top_customers_stmt)
        top_customers = [
            TopCustomerStat(
                customer_id=str(row.customer_id),
                customer_name=row.full_name or "Unknown",
                customer_email=row.email,
                total_orders=row.total_orders,
                total_spent=row.total_spent or Decimal('0'),
                favorite_order_type=None
            )
            for row in customers_result.all()
        ]
        
        comprehensive_stats = ComprehensiveStatsResponse(
            platform_overview=platform_overview,
            order_status_distribution=order_status_stats,
            payment_method_distribution=payment_stats,
            delivery_stats=delivery_stats,
            wallet_stats=wallet_stats,
            top_vendors=top_vendors,
            top_customers=top_customers
        )
        
        # Cache for 10 minutes
        redis_client.setex(cache_key, 600, comprehensive_stats.model_dump_json())
        
        return comprehensive_stats
        
    except Exception as e:
        logger.error(f"Error getting comprehensive stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve comprehensive statistics"
        )
