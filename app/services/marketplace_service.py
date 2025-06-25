from redis import Redis
from typing import Optional
import json
from uuid import UUID


from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert, select, update
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.sql.expression import and_
from app.models.models import (
    ChargeAndCommission,
    Item,
    Transaction,
    User,
    Order,
    OrderItem,
    Wallet,
)
from app.schemas.marketplace_schemas import ProductBuyRequest
from app.schemas.item_schemas import ItemType, ItemResponse
from app.schemas.order_schema import OrderResponseSchema, OrderType
from app.schemas.status_schema import (
    OrderStatus,
    PaymentStatus,
    RequireDeliverySchema,
    TransactionType,
)
from app.services.order_service import fetch_wallet
from app.utils.utils import get_fund_wallet_payment_link, get_user_notification_token
from app.config.config import redis_client


async def get_marketplace_items(db: AsyncSession) -> list[ItemResponse]:
    """Retrieves all marketplace items"""

    # Try cache first
    cached_items = redis_client.get(f"marketplace_items")
    if cached_items:
        item_dicts = json.loads(cached_items)
        return [ItemResponse(**item) for item in item_dicts]

    stmt = (
        select(Item)
        .where(Item.item_type == ItemType.PRODUCT)
        .options(joinedload(Item.images))
    )
    result = await db.execute(stmt)
    items = result.unique().scalars().all()

    item_list_dict = []
    for item in items:
        item_dict = {
            "name": item.name,
            "description": item.description,
            "price": item.price,
            "item_type": item.item_type,
            "category_id": item.category_id,
            "colors": item.colors,
            "stock": item.stock,
            "sizes": item.sizes,
            "id": item.id,
            "user_id": item.user_id,
            "images": [
                {"id": img.id, "url": img.url, "item_id": img.item_id}
                for img in item.images
            ],
        }
        item_list_dict.append(item_dict)

    # Cache the results
    if item_list_dict:
        redis_client.setex(
            "marketplace_items",
            CACHE_TTL,
            json.dumps(item_list_dict, default=str),
        )

    return [ItemResponse(**item) for item in item_list_dict]


async def get_marketplace_item(item_id: UUID, db: AsyncSession) -> ItemResponse:
    """Retrieves all marketplace items"""

    # Try cache first
    cached_item = redis_client.get(f"marketplace_items:{item_id}")
    if cached_item:
        item_dict = json.loads(cached_item)
        return ItemResponse(**item_dict)

    stmt = (
        select(Item)
        .where(Item.id == item_id)
        .where(Item.item_type == ItemType.PRODUCT)
        .options(joinedload(Item.images))
    )
    result = await db.execute(stmt)
    item = result.unique().scalar_one_or_none()

    item_dict = {
        "name": item.name,
        "description": item.description,
        "price": item.price,
        "item_type": item.item_type,
        "category_id": item.category_id,
        "colors": item.colors,
        "stock": item.stock,
        "sizes": item.sizes,
        "id": item.id,
        "user_id": item.user_id,
        "images": [
            {"id": img.id, "url": img.url, "item_id": img.item_id}
            for img in item.images
        ],
    }

    # Cache the results
    if item_dict:
        redis_client.setex(
            f"marketplace_items:{item_id}",
            CACHE_TTL,
            json.dumps(item_dict, default=str),
        )

    return ItemResponse(**item_dict)


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
    amount_due_vendor = total_cost - (total_cost * charge.product_commission_percentage)

    try:
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

        # UPDATE ITEM sTOCK
        for order_item in order.order_items:
            item = await db.execute(
                select(Item).where(
                    and_(
                        Item.id == order_item.item_id,
                        Item.user_id == order.vendor_id,
                    )
                )
            )
            await db.execute(
                update(Item)
                .where(Item.id == item.item_id)
                .values({"stock": item.quantity - order_item.quantity})
            )

        await db.commit()
        await db.refresh(order)

        token = await get_user_notification_token(db=db, user_id=order.vendor_id)

        if token:
            await send_push_notification(
                tokens=[token],
                title="New Order",
                message=f"You have a new order from {buyer.profile.full_name if buyer.profile.full_name else buyer.profile.business_name}",
                navigate_to="/delivery/orders",
            )

        set_cached_order(order.id, order.dict())

        return order
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to buy item"
        )


async def vendor_mark_item_delivered(
    order_id: UUID, current_user: User, db: AsyncSession
) -> OrderStatus:
    # order_result = await db.execute(select(Order).where(Order.id == order_id))

    order_result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.owner), selectinload(Order.vendor))
        .with_for_update()
    )

    order = order_result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    if current_user.id != order.vendor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized."
        )

    try:
        if order.order_status == OrderStatus.PENDING:
            order.order_status = OrderStatus.DELIVERED
            await db.commit
            await db.refresh(order)

            token = await get_user_notification_token(db=db, user_id=order.owner_id)

            if token:
                await send_push_notification(
                    tokens=[token],
                    title="Item Delivered",
                    message=f"Your item has been marked as delivered. Ensure it is what you ordered before marking as received.",
                    navigate_to="/delivery/orders",
                )

            return {"order_status": order.order_status}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to update order."
        )


async def owner_mark_item_received(
    order_id: UUID, current_user: User, db: AsyncSession
) -> OrderStatus:
    # order_result = await db.execute(select(Order).where(Order.id == order_id))

    order_result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.owner), selectinload(Order.vendor))
        .with_for_update()
    )

    order = order_result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    vendor_wallet = fetch_wallet(db=db, user_id=order.vendor_id)
    owner_wallet = fetch_wallet(db=db, user_id=order.owner_id)

    if current_user.id != order.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized."
        )

    try:
        if order.order_status == OrderStatus.DELIVERED:
            order.order_status = OrderStatus.RECEIVED

            await db.commit
            await db.refresh(order)

            await db.execute(
                update(Wallet)
                .where(Wallet.id == order.vendor_id)
                .values(
                    {
                        "balance": vendor_wallet.balance + order.amount_due_vendor,
                        "escrow_balance": vendor_wallet.escrow_balance
                        - order.amount_due_vendor
                        if vendor_wallet.escrow_balance >= order.amount_due_vendor
                        else 0,
                    }
                )
            )
            await db.execute(
                update(Wallet)
                .where(Wallet.id == order.owner_id)
                .values(
                    {
                        # "balance": vendor_wallet.balance  order.amount_due_vendor,
                        "escrow_balance": owner_wallet.escrow_balance
                        - order.total_price
                        if vendor_wallet.escrow_balance >= order.total_price
                        else 0,
                    }
                )
            )

            await db.execute(
                insert(Transaction).values(
                    wallet_id=vendor_wallet.id,
                    amount=order.amount_due_vendor,
                    payment_by=current_user.profile.full_name
                    if current_user.profile.full_name
                    else current_user.profile.business_name,
                    transaction_type=TransactionType.CREDIT,
                    payment_status=PaymentStatus.PAID,
                )
            )

            await db.execute(
                insert(Transaction).values(
                    wallet_id=owner_wallet.id,
                    amount=order.total_price,
                    payment_by=order.vendor.profile.full_name
                    if order.vendor.profile.full_name
                    else order.vendor.profile.business_name,
                    transaction_type=TransactionType.DEBIT,
                    payment_status=PaymentStatus.PAID,
                )
            )

            await db.commit()

            token = await get_user_notification_token(db=db, user_id=order.vendor_id)

            if token:
                await send_push_notification(
                    tokens=[token],
                    title="successful",
                    message=f"Transaction complete. \n The sum of ₦{order.amount_due_vendor} has released to your wallet",
                    navigate_to="/delivery/orders",
                )

            return {"order_status": order.order_status}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to update order."
        )


async def owner_mark_item_rejected(
    order_id: UUID, current_user: User, db: AsyncSession
) -> OrderStatus:
    # order_result = await db.execute(select(Order).where(Order.id == order_id))

    order_result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.owner), selectinload(Order.vendor))
        .with_for_update()
    )

    order = order_result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    if current_user.id != order.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized."
        )

    try:
        if order.order_status == OrderStatus.DELIVERED:
            order.order_status = OrderStatus.REJECTED
            await db.commit
            await db.refresh(order)

            token = await get_user_notification_token(db=db, user_id=order.vendor_id)

            if token:
                await send_push_notification(
                    tokens=[token],
                    title="Rejected",
                    message=f"Your item with order # {order.order_number} has been rejected.",
                    navigate_to="/delivery/orders",
                )

            return {"order_status": order.order_status}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to update order."
        )


async def vendor_mark_rejected_item_received(
    order_id: UUID, current_user: User, db: AsyncSession
) -> OrderStatus:
    # order_result = await db.execute(select(Order).where(Order.id == order_id))
    order_result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.owner), selectinload(Order.vendor))
        .with_for_update()
    )
    order = order_result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    vendor_wallet = fetch_wallet(db=db, user_id=order.vendor_id)
    owner_wallet = fetch_wallet(db=db, user_id=order.owner_id)

    if current_user.id != order.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized."
        )

    try:
        if order.order_status == OrderStatus.REJECTED:
            order.order_status = OrderStatus.RECEIVED
            await db.commit
            await db.refresh(order)

            await db.execute(
                update(Wallet)
                .where(Wallet.id == order.vendor_id)
                .values(
                    {
                        "balance": vendor_wallet.balance - order.amount_due_vendor,
                    }
                )
            )
            await db.commit()

            await db.execute(
                update(Wallet)
                .where(Wallet.id == order.owner_id)
                .values({"balance": owner_wallet.balance + order.total_price})
            )
            await db.commit()

            token = await get_user_notification_token(db=db, user_id=order.owner_id)

            if token:
                await send_push_notification(
                    tokens=[token],
                    title="Received",
                    message=f"Your rejected item with order #{order.order_number} been received by the vendour and the sum of ₦{order.total_price} has been released t your wallet.",
                    navigate_to="/delivery/orders",
                )

            return {"order_status": order.order_status}

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to update order."
        )


# <<<<< ---------- CACHE UTILITY ---------- >>>>>

CACHE_TTL = 3600


def get_cached_order(order_id: UUID) -> Optional[dict]:
    """Get order from cache"""
    cached_order = redis_client.get(f"order:{str(order_id)}")
    return json.loads(cached_order) if cached_order else None


def set_cached_order(order_id: UUID, order_data: dict) -> None:
    """Set order in cache"""
    redis_client.setex(
        f"product:{str(order_id)}", CACHE_TTL, json.dumps(order_data, default=str)
    )


def invalidate_order_cache(order_id: UUID, user_id: UUID, vendor_id: UUID) -> None:
    """Invalidate all related order caches"""
    redis_client.delete(f"product:{str(order_id)}")
    redis_client.delete(f"user_orders:{str(user_id)}")
    redis_client.delete(f"vendor_orders:{str(vendor_id)}")
    redis_client.delete("all_orders")
