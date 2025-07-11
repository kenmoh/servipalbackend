from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, date as _date
from decimal import Decimal
from enum import Enum


class StatsPeriod(str, Enum):
    """Time period options for statistics"""

    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    LAST_3_MONTHS = "last_3_months"
    CUSTOM = "custom"


class OrderStatsDaily(BaseModel):
    """Daily order statistics by type"""

    date: _date = Field(..., description="Date for the statistics")
    package: int = Field(default=0, description="Number of package orders")
    food: int = Field(default=0, description="Number of food orders")
    laundry: int = Field(default=0, description="Number of laundry orders")
    total: int = Field(default=0, description="Total orders for the day")

    class Config:
        orm_mode = True
        from_attributes = True


class OrderStatsResponse(BaseModel):
    """Response schema for order statistics"""

    period: StatsPeriod = Field(..., description="Time period for the statistics")
    start_date: _date = Field(..., description="Start date of the period")
    end_date: _date = Field(..., description="End date of the period")
    data: List[OrderStatsDaily] = Field(..., description="Daily order statistics")
    summary: Dict[str, int] = Field(..., description="Summary totals for the period")

    class Config:
        orm_mode = True
        from_attributes = True


class RevenueStatsDaily(BaseModel):
    """Daily revenue statistics"""

    date: _date = Field(..., description="Date for the statistics")
    total_revenue: Decimal = Field(
        default=Decimal("0"), description="Total revenue for the day"
    )
    order_revenue: Decimal = Field(
        default=Decimal("0"), description="Revenue from orders"
    )
    delivery_revenue: Decimal = Field(
        default=Decimal("0"), description="Revenue from delivery fees"
    )
    transaction_count: int = Field(default=0, description="Number of transactions")

    class Config:
        orm_mode = True
        from_attributes = True


class RevenueStatsResponse(BaseModel):
    """Response schema for revenue statistics"""

    period: StatsPeriod = Field(..., description="Time period for the statistics")
    start_date: _date = Field(..., description="Start date of the period")
    end_date: _date = Field(..., description="End date of the period")
    data: List[RevenueStatsDaily] = Field(..., description="Daily revenue statistics")
    summary: Dict[str, Decimal] = Field(
        ..., description="Summary totals for the period"
    )

    class Config:
        orm_mode = True
        from_attributes = True


class UserStatsDaily(BaseModel):
    """Daily user statistics"""

    date: _date = Field(..., description="Date for the statistics")
    new_users: int = Field(default=0, description="New user registrations")
    active_users: int = Field(default=0, description="Active users (placed orders)")
    total_users: int = Field(default=0, description="Cumulative total users")

    class Config:
        orm_mode = True
        from_attributes = True


class UserStatsResponse(BaseModel):
    """Response schema for user statistics"""

    period: StatsPeriod = Field(..., description="Time period for the statistics")
    start_date: _date = Field(..., description="Start date of the period")
    end_date: _date = Field(..., description="End date of the period")
    data: List[UserStatsDaily] = Field(..., description="Daily user statistics")
    summary: Dict[str, int] = Field(..., description="Summary totals for the period")

    class Config:
        orm_mode = True
        from_attributes = True


class TopVendorStat(BaseModel):
    """Top vendor statistics"""

    vendor_id: str = Field(..., description="Vendor user ID")
    vendor_name: str = Field(..., description="Vendor business name")
    vendor_email: str = Field(..., description="Vendor email")
    total_orders: int = Field(..., description="Total orders received")
    total_revenue: Decimal = Field(..., description="Total revenue generated")
    average_rating: Optional[float] = Field(None, description="Average rating")
    review_count: int = Field(default=0, description="Number of reviews")

    class Config:
        orm_mode = True
        from_attributes = True


class TopCustomerStat(BaseModel):
    """Top customer statistics"""

    customer_id: str = Field(..., description="Customer user ID")
    customer_name: str = Field(..., description="Customer name")
    customer_email: str = Field(..., description="Customer email")
    total_orders: int = Field(..., description="Total orders placed")
    total_spent: Decimal = Field(..., description="Total amount spent")
    favorite_order_type: Optional[str] = Field(
        None, description="Most frequent order type"
    )

    class Config:
        orm_mode = True
        from_attributes = True


class PlatformOverview(BaseModel):
    """Overall platform statistics"""

    total_users: int = Field(..., description="Total registered users")
    total_vendors: int = Field(..., description="Total vendors")
    total_customers: int = Field(..., description="Total customers")
    total_riders: int = Field(..., description="Total delivery riders")
    total_orders: int = Field(..., description="Total orders ever placed")
    total_revenue: Decimal = Field(..., description="Total platform revenue")
    pending_orders: int = Field(..., description="Current pending orders")
    completed_orders: int = Field(..., description="Total completed orders")
    active_users_today: int = Field(..., description="Users active today")
    orders_today: int = Field(..., description="Orders placed today")

    class Config:
        orm_mode = True
        from_attributes = True


class OrderStatusStats(BaseModel):
    """Order status distribution statistics"""

    pending: int = Field(default=0, description="Pending orders")
    confirmed: int = Field(default=0, description="Confirmed orders")
    preparing: int = Field(default=0, description="Orders being prepared")
    ready: int = Field(default=0, description="Ready orders")
    picked_up: int = Field(default=0, description="Picked up orders")
    delivered: int = Field(default=0, description="Delivered orders")
    cancelled: int = Field(default=0, description="Cancelled orders")
    total: int = Field(default=0, description="Total orders")

    class Config:
        orm_mode = True
        from_attributes = True


class PaymentMethodStats(BaseModel):
    """Payment method distribution statistics"""

    wallet: int = Field(default=0, description="Wallet payments")
    card: int = Field(default=0, description="Card payments")
    bank_transfer: int = Field(default=0, description="Bank transfer payments")
    total: int = Field(default=0, description="Total payments")

    class Config:
        orm_mode = True
        from_attributes = True


class DeliveryStats(BaseModel):
    """Delivery statistics"""

    total_deliveries: int = Field(..., description="Total deliveries")
    pending_deliveries: int = Field(..., description="Pending deliveries")
    completed_deliveries: int = Field(..., description="Completed deliveries")
    average_delivery_time: Optional[float] = Field(
        None, description="Average delivery time in minutes"
    )
    total_delivery_revenue: Decimal = Field(
        ..., description="Total delivery fee revenue"
    )

    class Config:
        orm_mode = True
        from_attributes = True


class WalletStats(BaseModel):
    """Wallet and transaction statistics"""

    total_wallets: int = Field(..., description="Total user wallets")
    total_wallet_balance: Decimal = Field(..., description="Sum of all wallet balances")
    total_escrow_balance: Decimal = Field(..., description="Sum of all escrow balances")
    total_transactions: int = Field(..., description="Total transactions")
    total_topups: int = Field(..., description="Total wallet top-ups")
    total_withdrawals: int = Field(..., description="Total withdrawals")
    topup_volume: Decimal = Field(..., description="Total top-up volume")
    withdrawal_volume: Decimal = Field(..., description="Total withdrawal volume")

    class Config:
        orm_mode = True
        from_attributes = True


class ComprehensiveStatsResponse(BaseModel):
    """Comprehensive platform statistics response"""

    platform_overview: PlatformOverview = Field(
        ..., description="Overall platform metrics"
    )
    order_status_distribution: OrderStatusStats = Field(
        ..., description="Order status breakdown"
    )
    payment_method_distribution: PaymentMethodStats = Field(
        ..., description="Payment method breakdown"
    )
    delivery_stats: DeliveryStats = Field(..., description="Delivery metrics")
    wallet_stats: WalletStats = Field(..., description="Wallet and transaction metrics")
    top_vendors: List[TopVendorStat] = Field(..., description="Top performing vendors")
    top_customers: List[TopCustomerStat] = Field(
        ..., description="Top customers by spending"
    )
    generated_at: datetime = Field(
        default_factory=datetime.now, description="When stats were generated"
    )

    class Config:
        orm_mode = True
        from_attributes = True


class StatsResponseWrapper(BaseModel):
    """Generic wrapper for stats responses"""

    success: bool = Field(
        default=True, description="Whether the request was successful"
    )
    message: str = Field(
        default="Statistics retrieved successfully", description="Response message"
    )
    data: Optional[Dict[str, Any]] = Field(None, description="Statistics data")
    generated_at: datetime = Field(
        default_factory=datetime.now, description="When stats were generated"
    )

    class Config:
        orm_mode = True
        from_attributes = True
