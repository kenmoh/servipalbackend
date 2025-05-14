from redis import Redis
from typing import Optional
import json
from uuid import UUID


from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.expression import and_
from app.models.models import ChargeAndCommission, Item, User, Order, OrderItem, Wallet
from app.schemas.marketplace_schemas import ProductBuyRequest
from app.schemas.order_schema import OrderResponseSchema, OrderType
from app.schemas.status_schema import OrderStatus, PaymentStatus, RequireDeliverySchema
from app.utils.utils import get_fund_wallet_payment_link
from app.config.config import redis_client


async def buy_product(
    db: AsyncSession,
    product_id: UUID,
    buyer: User,
    buy_request: ProductBuyRequest,
) -> OrderResponseSchema:
    """
    Handles the logic for a user buying a listed product.

    Args:
        db: The database session.
        product_id: The ID of the product being purchased.
        buyer: The authenticated user making the purchase.
        buy_request: The purchase details (e.g., quantity).

    Returns:
        The transaction record created for the purchase.

    Raises:
        HTTPException: Various exceptions for validation errors (not found, insufficient stock/funds, etc.).
    """

    # Use a single transaction block for atomicity
    async with db.begin():
        # 1. Fetch Item and Seller (using joinedload for efficiency)
        stmt_product = (
            select(Item)
            .where(Item.id == product_id)
            .options(selectinload(Item.vendor))  # Load seller(vendor) info
            .with_for_update()  # Lock the product row for update to prevent race conditions on stock
        )
        result_product = await db.execute(stmt_product)
        product = result_product.scalar_one_or_none()

        charge_result = await db.execute(select(ChargeAndCommission))
        charge = charge_result.scalars().first()

        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
            )

        if not product.vendor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Item seller not found"
            )

        # 2. Check Buyer is not Seller
        if product.user_id == buyer.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Seller cannot buy their own product",
            )

        # 3. Check Stock Availability
        if not product.in_stock or product.stock < buy_request.quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient stock"
            )

        total_cost = product.price * buy_request.quantity
        amount_due_vendor = total_cost - (
            total_cost * charge.product_commission_percentage
        )

        # Create order
        order = Order(
            owner_id=buyer.id,
            vendor_id=product.user_id,
            order_type=OrderType.PRODUCT,
            total_price=total_cost,
            order_status=OrderStatus.PENDING,
            amount_due_vendor=amount_due_vendor,
            order_payment_status=PaymentStatus.PENDING,
            require_delivery=RequireDeliverySchema.PICKUP,
        )
        db.add(order)
        await db.flush()

        # Create order item
        order_item = OrderItem(
            order_id=order.id,
            item_id=product.id,
            quantity=buy_request.quantity,
            colors=buy_request.colors,
            sizes=buy_request.sizes,
            additional_info=buy_request.additional_info,
        )
        db.add(order_item)
        await db.flush()

        # Generate payment link
        payment_link = get_fund_wallet_payment_link(
            id=product_id, current_user=buyer, amount=total_cost
        )

        # Store link with the order
        order.payment_link = payment_link

        # Update item stock
        # product.stock -= buy_request.quantity
        # product.in_stock = product.stock > 0

        await db.commit()
        await db.refresh(order)

        set_cached_order(order.id, order.dict())

        return order


async def update_item_order_status(
    order_id: UUID, new_status: OrderStatus, current_user: User, db: AsyncSession
) -> OrderResponseSchema:
    """
    Updates the status of a product order based on user role and allowed transitions.

    Args:
        order_id: ID of the order to update.
        new_status: The desired new order status.
        current_user: The user attempting to update the order.
        db: Async database session.

    Returns:
        The updated Order object.

    Raises:
        HTTPException: If order not found, update is unauthorized, or invalid status transition.
    """

    # Try cache first
    cached_order = await get_cached_order(order_id)
    if cached_order:
        # Verify user permissions using cached data
        if (
            current_user.id != cached_order["owner_id"]
            and current_user.id != cached_order["vendor_id"]
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized to update this order",
            )
    async with db.begin():
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.owner), selectinload(Order.vendor))
            .with_for_update()
        )

        result = await db.execute(stmt)
        order = result.scalar_one_or_none()

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
            )

        # Buyer actions
        # Buyer (owner in the Order model)
        if current_user.id == order.owner_id:
            if (
                new_status == OrderStatus.CANCELLED
                and order.order_status != OrderStatus.DELIVERED
            ):
                order.order_status = new_status
            elif (
                new_status == OrderStatus.REJECTED
                and order.order_status == OrderStatus.DELIVERED
            ):
                order.order_status = new_status
            elif (
                new_status == OrderStatus.RECEIVED
                and order.order_status == OrderStatus.DELIVERED
            ):
                order.order_status = new_status

                vendor_wallet = await db.get(Wallet, order.vendor_id)
                vendor_wallet.balance += order.amount_due_vendor
                vendor_wallet.escrow_balance -= (
                    order.amount_due_vendor
                    if vendor_wallet.escrow_balance >= order.amount_due_vendor
                    else vendor_wallet.escrow_balance
                )
                # await db.commit()

            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status update for buyer: {new_status}",
                )

        # Vendor actions
        elif current_user.id == order.vendor_id:  # Vendor
            if (
                new_status == OrderStatus.DELIVERED
                and order.order_status == OrderStatus.PENDING
            ):
                order.order_status = new_status
            # Assuming vendor can cancel before delivery
            elif (
                new_status == OrderStatus.CANCELLED
                and order.order_status == OrderStatus.PENDING
            ):
                order.order_status = new_status
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status update for vendor: {new_status}",
                )

        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Unauthorized to update this order",
            )

        await db.commit()
        await db.refresh(order)

        # Invalidate caches after successful update
        await invalidate_order_cache(
            order_id=order.id, user_id=order.owner_id, vendor_id=order.vendor_id
        )
        return order


# <<<<< ---------- CACHE UTILITY ---------- >>>>>

CACHE_TTL = 3600


def get_cached_order(order_id: UUID) -> Optional[dict]:
    """Get order from cache"""
    cached_order = redis_client.get(f"order:{str(order_id)}")
    return json.loads(cached_order) if cached_order else None


def set_cached_order(order_id: UUID, order_data: dict) -> None:
    """Set order in cache"""
    redis_client.setex(
        f"order:{str(order_id)}", CACHE_TTL, json.dumps(
            order_data, default=str)
    )


def invalidate_order_cache(order_id: UUID, user_id: UUID, vendor_id: UUID) -> None:
    """Invalidate all related order caches"""
    redis_client.delete(f"order:{str(order_id)}")
    redis_client.delete(f"user_orders:{str(user_id)}")
    redis_client.delete(f"vendor_orders:{str(vendor_id)}")
    redis_client.delete("all_orders")
