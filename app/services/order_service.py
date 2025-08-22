from curses import meta
from datetime import timedelta, datetime
from typing import Optional
import uuid
from sqlalchemy import func, or_, and_, select, update, insert

# from sqlalchemy.sql.expression.ColumnOperators import in_
from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload
from app.models.models import (
    AuditLog,
    ChargeAndCommission,
    Delivery,
    Item,
    Order,
    OrderItem,
    Review,
    Transaction,
    User,
    Wallet,
    ItemImage,
    Profile,
)
from app.queue import notification_queue, order_status_queue
from app.services import ws_service
from app.queue.producer import producer


import json
from decimal import Decimal
from uuid import UUID, uuid1

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.schemas import ReviewSchema
from app.schemas.status_schema import (
    OrderStatus,
    PaymentMethod,
    TransactionDirection,
    TransactionType,
)


from app.schemas.order_schema import (
    OrderType,
    PaymentStatus,
    OrderItemCreate,
    PackageCreate,
    DeliveryStatusUpdateSchema,
    OrderAndDeliverySchema,
)
from app.schemas.delivery_schemas import (
    DeliveryResponse,
    DeliveryType,
    PaginatedDeliveryResponse,
)
from app.schemas.item_schemas import ItemType


from app.schemas.status_schema import RequireDeliverySchema, DeliveryStatus
from app.schemas.user_schemas import UserType, WalletRespose
from app.utils import logger_config
from app.utils.utils import (
    get_dispatch_id,
    get_payment_link,
    send_push_notification,
    get_user_notification_token,
)
from app.config.config import redis_client, settings
from app.utils.s3_service import add_image


ALL_DELIVERY = "orders"


async def get_delivery_by_order_id(
    order_id: UUID,
    db: AsyncSession,
) -> DeliveryResponse:
    """Get delivery by order ID"""

    try:
        order_stmt = (
            select(Order)
            .options(
                selectinload(Order.delivery),
                selectinload(Order.order_items)
                .selectinload(OrderItem.item)
                .selectinload(Item.images),
                joinedload(Order.vendor).joinedload(User.profile),
            )
            .where(Order.id == order_id)
        )

        order_result = await db.execute(order_stmt)
        order = order_result.scalar_one_or_none()

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
            )

        oder_response = format_delivery_response(order, order.delivery)

        return oder_response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving delivery: {str(e)}",
        )


async def get_user_orders(db: AsyncSession, user_id: UUID) -> list[DeliveryResponse]:
    """
    Get all orders with their deliveries (if any) with caching
    """
    cache_key = f"user_orders:{user_id}"

    # Try cache first with error handling

    cached_deliveries = redis_client.get(cache_key)
    if cached_deliveries:
        return [DeliveryResponse(**d) for d in json.loads(cached_deliveries)]

    stmt = (
        select(Order)
        .where(or_(Order.owner_id == user_id, Order.vendor_id == user_id))
        .where(
            Order.order_type.in_([OrderType.FOOD, OrderType.LAUNDRY, OrderType.PACKAGE])
        )
        .order_by(Order.updated_at.desc())
        .options(
            selectinload(Order.order_items).options(
                joinedload(OrderItem.item).options(selectinload(Item.images))
            ),
            joinedload(Order.delivery),
            joinedload(Order.vendor).joinedload(User.profile),
        )
    )

    result = await db.execute(stmt)
    orders = result.unique().scalars().all()

    # Format responses - delivery will be None for orders without delivery
    delivery_responses = [
        format_delivery_response(order, order.delivery) for order in orders
    ]

    # Cache the formatted responses with error handling

    redis_client.setex(
        cache_key,
        timedelta(seconds=CACHE_TTL),
        json.dumps([d.model_dump() for d in delivery_responses], default=str),
    )

    return delivery_responses


async def get_all_orders(
    db: AsyncSession, skip: int = 0, limit: int = 20
) -> list[DeliveryResponse]:
    """
    Get all orders with their deliveries (if any) with caching
    """
    cache_key = f"ALL_DELIVERY-{skip}-{limit}"

    # Try cache first with error handling

    cached_deliveries = redis_client.get(cache_key)
    if cached_deliveries:
        return [DeliveryResponse(**d) for d in json.loads(cached_deliveries)]

    stmt = (
        select(Order)
        .offset(skip)
        .limit(limit)
        .options(
            selectinload(Order.order_items).options(
                joinedload(OrderItem.item).options(selectinload(Item.images))
            ),
            joinedload(Order.delivery),
            joinedload(Order.vendor).joinedload(User.profile),
        )
        .where(Order.require_delivery == RequireDeliverySchema.PICKUP)
        .where(
            Order.order_type.in_([OrderType.FOOD, OrderType.LAUNDRY, OrderType.PACKAGE])
        )
        .order_by(Order.created_at.desc())
    )

    result = await db.execute(stmt)
    orders = result.unique().scalars().all()

    # Format responses - delivery will be None for orders without delivery
    delivery_responses = [
        format_delivery_response(order, order.delivery) for order in orders
    ]

    # Cache the formatted responses with error handling

    redis_client.setex(
        cache_key,
        timedelta(seconds=CACHE_TTL),
        json.dumps([d.model_dump() for d in delivery_responses], default=str),
    )

    return delivery_responses


async def get_all_require_delivery_orders(
    db: AsyncSession, skip: int = 0, limit: int = 20
) -> PaginatedDeliveryResponse:
    """
    Get all orders with their deliveries (if any) with caching and total count
    """
    cache_key = f"require_delivery_orders-{skip}-{limit}"

    # Try cache first with error handling
    cached_deliveries = redis_client.get(cache_key)
    if cached_deliveries:
        cached = json.loads(cached_deliveries)
        return cached

    # 1. Get total count (without skip/limit)
    total_stmt = (
        select(func.count())
        .select_from(Order)
        .where(Order.require_delivery == RequireDeliverySchema.DELIVERY)
    )
    total_result = await db.execute(total_stmt)
    total = total_result.scalar_one()

    # 2. Get paginated data
    stmt = (
        select(Order)
        .offset(skip)
        .limit(limit)
        .options(
            selectinload(Order.order_items).options(
                joinedload(OrderItem.item).options(selectinload(Item.images))
            ),
            joinedload(Order.delivery),
            joinedload(Order.vendor).joinedload(User.profile),
        )
        .where(Order.require_delivery == RequireDeliverySchema.DELIVERY)
        .where(
            Order.order_type.in_([OrderType.FOOD, OrderType.LAUNDRY, OrderType.PACKAGE])
        )
        .order_by(Order.created_at.desc())
    )

    result = await db.execute(stmt)
    orders = result.unique().scalars().all()

    delivery_responses = [
        format_delivery_response(order, order.delivery) for order in orders
    ]

    response = {"data": [d.model_dump() for d in delivery_responses], "total": total}

    # Cache the formatted responses with error handling
    redis_client.setex(
        cache_key,
        timedelta(seconds=CACHE_TTL),
        json.dumps(response, default=str),
    )

    return response


async def get_all_pickup_delivery_orders(
    db: AsyncSession, skip: int = 0, limit: int = 20
) -> PaginatedDeliveryResponse:
    """
    Get all orders with their deliveries (if any) with caching and total count
    """
    cache_key = f"pickup_delivery_orders-{skip}-{limit}"

    # Try cache first with error handling
    cached_deliveries = redis_client.get(cache_key)
    if cached_deliveries:
        cached = json.loads(cached_deliveries)
        return cached

    # 1. Get total count (without skip/limit)
    total_stmt = (
        select(func.count())
        .select_from(Order)
        .where(Order.require_delivery == RequireDeliverySchema.DELIVERY)
        .where(
            Order.order_type.in_([OrderType.FOOD, OrderType.LAUNDRY, OrderType.PACKAGE])
        )
    )
    total_result = await db.execute(total_stmt)
    total = total_result.scalar_one()

    # 2. Get paginated data
    stmt = (
        select(Order)
        .offset(skip)
        .limit(limit)
        .options(
            selectinload(Order.order_items).options(
                joinedload(OrderItem.item).options(selectinload(Item.images))
            ),
            joinedload(Order.delivery),
            joinedload(Order.vendor).joinedload(User.profile),
        )
        .where(Order.require_delivery == RequireDeliverySchema.PICKUP)
        .where(
            Order.order_type.in_([OrderType.FOOD, OrderType.LAUNDRY, OrderType.PACKAGE])
        )
        .order_by(Order.created_at.desc())
    )

    result = await db.execute(stmt)
    orders = result.unique().scalars().all()

    delivery_responses = [
        format_delivery_response(order, order.delivery) for order in orders
    ]

    response = {"data": [d.model_dump() for d in delivery_responses], "total": total}

    # Cache the formatted responses with error handling
    redis_client.setex(
        cache_key,
        timedelta(seconds=CACHE_TTL),
        json.dumps(response, default=str),
    )

    return response


async def create_package_order(
    db: AsyncSession, data: PackageCreate, image: UploadFile, current_user: User
) -> DeliveryResponse:
    if current_user.user_type == UserType.CUSTOMER and not (
        current_user.profile.full_name and current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and full name are required. Please update your profile!",
        )
    if current_user.user_type in [
        UserType.RESTAURANT_VENDOR,
        UserType.LAUNDRY_VENDOR,
    ] and not (
        current_user.profile.business_name and current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and business name are required. Please update your profile!",
        )
    if current_user.user_type in [UserType.RIDER, UserType.DISPATCH]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not allowed to perform this action.",
        )
    try:
        # --- 1. Insert into 'packages' table ---
        package_insert_result = await db.execute(
            insert(Item)
            .values(
                {
                    "user_id": current_user.id,
                    "item_type": ItemType.PACKAGE,
                    "name": data.name,
                    "description": data.description,
                }
            )
            .returning(Item.name, Item.id, Item.user_id)
        )

        package_data = package_insert_result.fetchone()

        image_url = await add_image(image)

        item_image = ItemImage(item_id=package_data.id, url=image_url)
        db.add(item_image)

        order_insert_result = await db.execute(
            insert(Order)
            .values(
                {
                    "owner_id": package_data.user_id,
                    "vendor_id": package_data.user_id,  # Review this logic
                    "order_type": DeliveryType.PACKAGE,
                    "amount_due_vendor": 0,
                    "order_status": OrderStatus.PENDING,
                    "order_payment_status": PaymentStatus.PENDING,
                    "require_delivery": RequireDeliverySchema.DELIVERY,
                }
            )
            .returning(Order.id, Order.tx_ref, Order.owner_id, Order.vendor_id)
        )

        order_data = order_insert_result.fetchone()
        package_item_payload = [
            {
                "order_id": order_data.id,
                "item_id": package_data.id,
                "quantity": 1,
            }
        ]

        # Insert all items in one go
        await db.execute(insert(OrderItem).values(package_item_payload))

        # --- 3. Calculate Delivery Fee (Needs distance!) ---
        delivery_fee = await calculate_delivery_fee(data.distance, db)
        amount_due_dispatch = await calculate_amount_due_dispatch(db, delivery_fee)

        # --- 4. Insert into 'deliveries' table ---

        delivery_insert_result = await db.execute(
            insert(Delivery)
            .values(
                {
                    "order_id": order_data.id,
                    "delivery_type": DeliveryType.PACKAGE,
                    "delivery_status": DeliveryStatus.PENDING,
                    "sender_id": current_user.id,
                    "vendor_id": current_user.id,
                    "pickup_coordinates": data.pickup_coordinates,
                    "dropoff_coordinates": data.dropoff_coordinates,
                    "delivery_fee": delivery_fee,
                    "amount_due_dispatch": amount_due_dispatch,
                    "distance": data.distance,
                    "duration": data.duration,
                    "origin": data.origin,
                    "destination": data.destination,
                    "sender_phone_number": current_user.profile.phone_number,
                }
            )
            .returning(
                Delivery.id,
                Delivery.order_id,
                Delivery.delivery_fee,
                Delivery.vendor_id,
            )
        )

        delivery_data = delivery_insert_result.fetchone()

        # --- 5. Generate Payment Link ---
        total_amount_due = delivery_data.delivery_fee

        payment_link = await get_payment_link(
            tx_ref=order_data.tx_ref,
            amount=delivery_data.delivery_fee,
            current_user=current_user,
        )

        # --- 6. Update Order with payment link and total price ---

        await db.execute(
            update(Order)
            .where(Order.id == delivery_data.order_id)
            .values(
                {
                    "payment_link": payment_link,
                    "total_price": total_amount_due,
                    "grand_total": total_amount_due,
                }
            )
        )

        await db.commit()

        invalidate_order_cache(delivery_data.order_id)

        redis_client.delete(f"user_orders:{current_user.id}")
        redis_client.delete(f"user_orders:{order_data.owner_id}")
        redis_client.delete(f"user_orders:{order_data.vendor_id}")
        redis_client.delete(f"{ALL_DELIVERY}")
        redis_client.delete("paid_pending_deliveries")
        redis_client.delete(f"user_related_orders:{current_user.id}")
        redis_client.delete("orders")
        if hasattr(delivery_data, "vendor_id"):
            redis_client.delete(f"vendor_orders:{delivery_data.vendor_id}")
        else:
            redis_client.delete(f"vendor_orders:{current_user.id}")

        stmt = (
            select(Order)
            .where(Order.id == delivery_data.order_id)
            .options(
                selectinload(Order.order_items).options(
                    joinedload(OrderItem.item).options(selectinload(Item.images))
                )
            )
        )
        order = (await db.execute(stmt)).scalar_one()

        delivery_stmt = select(Delivery).where(Delivery.id == delivery_data.id)
        delivery = (await db.execute(delivery_stmt)).scalar_one()

        redis_client.delete("paid_pending_deliveries")
        redis_client.delete(f"user_related_orders:{current_user.id}")

        await ws_service.broadcast_new_order({"order_id": order.id})

        # REUSE the formatting function
        return format_delivery_response(order, delivery)

    except Exception as e:
        # Rollback
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create package order: {e}",
        )


async def order_food_or_request_laundy_service(
    current_user: User,
    db: AsyncSession,
    vendor_id: UUID,
    order_item: OrderAndDeliverySchema,
) -> DeliveryResponse:
    """
    Creates a meal or laundry order and its associated delivery record.
    """

    # Validate profile info based on user type
    if current_user.user_type == UserType.CUSTOMER and not (
        current_user.profile.full_name and current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and full name are required. Please update your profile!",
        )
    if current_user.user_type in [
        UserType.LAUNDRY_VENDOR,
        UserType.RESTAURANT_VENDOR,
    ] and not (
        current_user.profile.business_name and current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and business name are required. Please update your profile!",
        )

    if current_user.user_type in [UserType.RIDER, UserType.DISPATCH]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not allowed to perform this action!",
        )
    for item_order in order_item.order_items:
        if current_user.id == item_order.vendor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot order your own item(s)!",
            )
    for vendor_item in order_item.order_items:
        if vendor_item.vendor_id != vendor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Item(s) must belong to the same vendour!",
            )

    # Batch fetch all items at once - filter by vendor_id for additional validation
    item_ids = [
        UUID(item.item_id) if isinstance(item.item_id, str) else item.item_id
        for item in order_item.order_items
    ]
    items_result = await db.execute(
        select(Item).where(Item.id.in_(item_ids)).where(Item.user_id == vendor_id)
    )
    items_data = {item.id: item for item in items_result.scalars().all()}

    # Validate all items exist and belong to the vendor
    if len(items_data) != len(item_ids):
        found_items = set(items_data.keys())
        missing_items = set(item_ids) - found_items
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Items not found or don't belong to this vendor: {missing_items}",
        )

    # Calculate totals
    total_price = Decimal("0.00")
    item_types = set()

    for order_item_detail in order_item.order_items:
        # Convert string UUID to UUID object for dictionary lookup
        item_uuid = (
            UUID(order_item_detail.item_id)
            if isinstance(order_item_detail.item_id, str)
            else order_item_detail.item_id
        )
        item_data = items_data[item_uuid]

        # Price and type calculation
        total_price += Decimal(item_data.price) * Decimal(order_item_detail.quantity)
        item_types.add(item_data.item_type)

    # Validate single item type
    if len(item_types) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All items in the order must be of the same type (either all food or all laundry items)",
        )

    item_type = item_types.pop()
    amount_due_vendor = await calculate_amount_due_vendor(db, order_item.order_items)

    try:
        # Determine if delivery is required
        requires_delivery = (
            order_item.require_delivery == RequireDeliverySchema.DELIVERY
        )

        # Calculate delivery details if needed
        delivery_fee = Decimal("0.00")
        amount_due_dispatch = Decimal("0.00")
        final_amount = total_price

        if requires_delivery:
            delivery_fee = await calculate_delivery_fee(order_item.distance, db)
            amount_due_dispatch = await calculate_amount_due_dispatch(db, delivery_fee)
            final_amount = total_price + delivery_fee

        # Create the order
        order_insert_result = await db.execute(
            insert(Order)
            .values(
                {
                    "owner_id": current_user.id,
                    "vendor_id": vendor_id,
                    "order_type": item_type,
                    "require_delivery": order_item.require_delivery,
                    "total_price": total_price,
                    "order_payment_status": PaymentStatus.PENDING,
                    "order_status": OrderStatus.PENDING,
                    "amount_due_vendor": amount_due_vendor,
                    "additional_info": order_item.additional_info,
                }
            )
            .returning(Order.id, Order.tx_ref)
        )

        order_id, tx_ref = order_insert_result.scalar_one()

        # Create order items
        order_items_payload = [
            {
                "order_id": order_id,
                "item_id": item.item_id,
                "quantity": item.quantity,
            }
            for item in order_item.order_items
        ]
        await db.execute(insert(OrderItem).values(order_items_payload))

        # Create delivery if required
        delivery_id = None
        if requires_delivery:
            await db.execute(
                insert(Delivery)
                .values(
                    {
                        "order_id": order_id,
                        "vendor_id": vendor_id,
                        "sender_id": current_user.id,
                        "delivery_type": item_type,
                        "delivery_status": DeliveryStatus.PENDING,
                        "pickup_coordinates": order_item.pickup_coordinates,
                        "dropoff_coordinates": order_item.dropoff_coordinates,
                        "distance": Decimal(order_item.distance),
                        "duration": order_item.duration,
                        "delivery_fee": delivery_fee,
                        "amount_due_dispatch": amount_due_dispatch,
                        "origin": order_item.origin,
                        "destination": order_item.destination,
                        "sender_phone_number": current_user.profile.phone_number,
                    }
                )
                .returning(Delivery.id)
            )

            # delivery_id = delivery_insert_result.scalar_one()

        # Generate payment link
        payment_link = await get_payment_link(tx_ref, final_amount, current_user)

        # Update order with payment link
        await db.execute(
            update(Order)
            .where(Order.id == order_id)
            .values({"payment_link": payment_link, "grand_total": final_amount})
        )

        await db.commit()

        # Clear relevant caches
        cache_keys = [
            f"user_orders:{current_user.id}",
            f"vendor_orders:{vendor_id}",
            f"order_details:{order_id}",
            f"user_orders:{current_user.id}",
            f"user_orders:{vendor_id}",
        ]
        redis_client.delete(*cache_keys)
        redis_client.delete(ALL_DELIVERY)
        redis_client.delete("orders")

        # Single optimized query to fetch complete order and delivery data
        if requires_delivery:
            stmt = (
                select(Order, Delivery)
                .join(Delivery, Order.id == Delivery.order_id)
                .where(Order.id == order_id)
                .options(
                    selectinload(Order.order_items).options(
                        joinedload(OrderItem.item).options(selectinload(Item.images))
                    ),
                    joinedload(Order.vendor).joinedload(User.profile),
                )
            )
            result = await db.execute(stmt)
            order, delivery = result.first()
            return format_delivery_response(order, delivery)
        else:
            stmt = (
                select(Order)
                .where(Order.id == order_id)
                .options(
                    selectinload(Order.order_items).options(
                        joinedload(OrderItem.item).options(selectinload(Item.images))
                    ),
                    joinedload(Order.vendor).joinedload(User.profile),
                )
            )
            order = (await db.execute(stmt)).scalar_one()

            redis_client.delete(f"{ALL_DELIVERY}")

            await ws_service.broadcast_new_order({"order_id": order.id})

            token = await get_user_notification_token(db=db, user_id=vendor_id)

            if token:
                await send_push_notification(
                    tokens=[token],
                    title="New Order",
                    message=f"You have a new order from {current_user.profile.full_name if current_user.profile.full_name else current_user.profile.business_name}",
                    navigate_to="/delivery/orders",
                )
            redis_client.delete("paid_pending_deliveries")
            redis_client.delete(f"user_related_orders:{current_user.id}")
            redis_client.delete(f"user_orders:{order.owner_id}")
            redis_client.delete(f"user_orders:{order.vendor_id}")
            redis_client.delete("orders")
            return format_delivery_response(order, delivery=None)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create order - {e}",
        )


async def _cancel_delivery_and_order(
    db: AsyncSession, order_id: UUID
) -> DeliveryStatusUpdateSchema:
    """
    Helper to set delivery and order status to cancelled and log the audit.
    Args:
        db: Database session
        order: Order object
        delivery_status
        order_status: New order status

    """

    order_stmt = (
        select(Order).options(selectinload(Order.delivery)).where(Order.id == order_id).with_for_update()
    )

    order_result = await db.execute(order_stmt)
    order = order_result.scalar_one_or_none()

    try:
        if (
            order.require_delivery == RequireDeliverySchema.DELIVERY
            and order.delivery.delivery_status
            not in [
                DeliveryStatus.DELIVERED,
                DeliveryStatus.RECEIVED,
                DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM,
            ]
        ):
            # Set delivery status
            await db.execute(
                update(Delivery)
                .where(Delivery.id == order.delivery.id)
                .values(delivery_status=DeliveryStatus.CANCELLED)
            )
            # Set order status
            await db.execute(
                update(Order)
                .where(Order.id == order.id)
                .values(order_status=OrderStatus.CANCELLED)
            )
            # Commit after both updates and audit
            await db.commit()
            await db.refresh(order)

            return DeliveryStatusUpdateSchema(
                order_status=order.order.order_status,
                delivery_status=order.delivery.delivery_status,
            )

        elif (
            order.require_delivery == RequireDeliverySchema.PICKUP
            and order.order_status
            not in [
                OrderStatus.DELIVERED,
                OrderStatus.RECEIVED,
            ]
        ):
            await db.execute(
                update(Order)
                .where(Order.id == order.id)
                .values(order_status=OrderStatus.CANCELLED)
            )
            await db.commit()
            await db.refresh(order)

            return DeliveryStatusUpdateSchema(order_status=order.order.order_status)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel order - {e}",
        )


async def _update_delivery_and_order(
    db: AsyncSession,
    order_id: UUID,
    delivery_status: DeliveryStatus = None,
    order_status: OrderStatus = None,
) -> DeliveryResponse:
    """
    Update delivery and/or order status.
    Args:
        db: Database session
        order_id: UUID of the order
        delivery_status: New status for delivery (optional)
        order_status: New status for order (optional)
    Returns:
        DeliveryResponse
    """
    order_stmt = (
        select(Order).options(selectinload(Order.delivery)).where(Order.id == order_id).with_for_update()
    )
    order_result = await db.execute(order_stmt)
    order = order_result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    try:
        # Update delivery if needed
        if (
            order.require_delivery == RequireDeliverySchema.DELIVERY
            and order.delivery
            and delivery_status
        ):
            await db.execute(
                update(Delivery)
                .where(Delivery.id == order.delivery.id)
                .values(delivery_status=delivery_status)
            )
        # Update order if needed
        if order_status:
            await db.execute(
                update(Order)
                .where(Order.id == order.id)
                .values(order_status=order_status)
            )
        await db.commit()
        # Refresh order and delivery
        await db.refresh(order)
        if order.delivery:
            await db.refresh(order.delivery)
        return format_delivery_response(order, order.delivery)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update delivery/order - {e}",
        )


async def cancel_order_or_delivery(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Cancel a delivery (Owner/Rider/Dispatch). This sets both the delivery and related order status to CANCELLED,
    updates wallet/escrow as needed, and logs an audit entry for traceability.
    Args:
        db: Database session
        delivery_id: UUID of the delivery to cancel
        current_user: User performing the cancellation
    Returns:
        DeliveryStatusUpdateSchema with the new delivery status
    """
    wallet_result = await db.execute(select(Wallet).where(Wallet.id == current_user.id))
    wallet = wallet_result.scalar_one_or_none()
    order = await _cancel_delivery_and_order(db=db, order_id=order_id)

    if current_user.id not in [
        order.delivery.sender_id,
        order.delivery.vendor_id,
        order.delivery.rider_id,
        order.order.owner_id,
        order.order.vendor_id,
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to cancel this order.",
        )

    if current_user.user_type in [
        UserType.CUSTOMER,
        UserType.LAUNDRY_VENDOR,
        UserType.RESTAURANT_VENDOR,
    ]:
        total_amount = (
            max(order.delivery.delivery_fee, 0) + max(order.order.total_price, 0)
            if order.order_type
            in [OrderType.FOOD, OrderType.LAUNDRY, OrderType.PRODUCT]
            else max(order.delivery.delivery_fee, 0)
        )

        # UPDATE SENDER WALLET
        new_escrow = max(wallet.escrow_balance - total_amount, 0)
        new_balance = max(wallet.balance + total_amount, 0)

        await db.execute(
            update(Wallet)
            .where(Wallet.id == current_user.id)
            .values(
                {
                    "balance": new_balance,
                    "escrow_balance": new_escrow,
                }
            )
        )
        await db.commit()
        await db.refresh(order)

        invalidate_delivery_cache(order.delivery.id)
        redis_client.delete("paid_pending_deliveries")
        redis_client.delete(f"user_related_orders:{current_user.id}")
        redis_client.delete("orders")
        return DeliveryStatusUpdateSchema(delivery_status=order.order.order_status)

    if current_user.user_type in [
        UserType.RIDER,
        UserType.DISPATCH,
    ] and current_user.id in [order.delivery.rider_id, order.delivery.dispatch_id]:
        if order.delivery.delivery_status == DeliveryStatus.ACCEPTED:
            # await _cancel_delivery_and_order(db=db, order_id=order_id)

            current_user.order_cancel_count += 1
            await db.commit()

            # UPDATE RIDER ESCROW BALANCE
            new_escrow = max(
                wallet.escrow_balance - order.delivery.amount_due_dispatch, 0
            )
            await db.execute(
                update(Wallet)
                .where(Wallet.id == order.delivery.dispatch_id)
                .values({"escrow_balance": new_escrow})
            )

            await db.commit()
            await db.refresh(order)

            invalidate_delivery_cache(order.delivery.id)
            redis_client.delete(f"{ALL_DELIVERY}")

            token = await get_user_notification_token(
                db=db, user_id=order.delivery.vendor_id
            )
           

            if token:
                await send_push_notification(
                    tokens=[token],
                    title="Order canceled",
                    message="Your Order has been canceled and will be re-listed",
                    navigate_to="/(app)/delivery/orders",
                )

            # if token:
            #     await send_push_notification(
            #         tokens=[token],
            #         title="Order canceled",
            #         message="Your Order has been canceled and will be re-listed",
            #         navigate_to="/(app)/delivery/orders",
            #     )
            # if rider_token:
            #     await send_push_notification(
            #         tokens=[rider_token],
            #         title="Order canceled",
            #         message="You canceled this order.",
            #         navigate_to="/(app)/delivery/orders",
            #     )

            redis_client.delete(f"{ALL_DELIVERY}")
            redis_client.delete("paid_pending_deliveries")
            redis_client.delete(f"user_related_orders:{current_user.id}")
            redis_client.delete("orders")

            await ws_service.broadcast_delivery_status_update(
                delivery_id=order.delivery.id,
                delivery_status=order.delivery.delivery_status,
            )
            await order_status_queue.publish_order_update(
                order_id=str(order.id),
                new_status=order.delivery.delivery_status,
                delivery_id=order.delivery.id,
                cache_keys=[
                    "paid_pending_deliveries",
                    f"{ALL_DELIVERY}",
                    f"user_related_orders:{current_user.id}",
                    "orders",
                ],
            )
            return DeliveryStatusUpdateSchema(delivery_status=order.order.order_status)


async def re_list_item_for_delivery(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Re-list delivery(Owner)
    """
    wallet_result = await db.execute(select(Wallet).where(Wallet.id == current_user.id))

    wallet = wallet_result.scalar_one_or_none()

    if current_user.user_type in [
        UserType.CUSTOMER,
        UserType.LAUNDRY_VENDOR,
        UserType.RESTAURANT_VENDOR,
    ]:
        # result = await db.execute(
        #     select(Delivery)
        #     .where(Delivery.id == order.delivery.id)
        #     .where(Delivery.sender_id == current_user.id)
        # )
        # delivery = result.scalar_one_or_none()

        try:
            order = await _update_delivery_and_order(
                db=db, order_id=order_id, delivery_status=DeliveryStatus.PENDING
            )

            # if delivery.delivery_status == DeliveryStatus.CANCELLED:
            #     result = await db.execute(
            #         update(Delivery)
            #         .where(Delivery.id == order.delivery.id)
            #         .where(Delivery.sender_id == current_user.id)
            #         .values(delivery_status=DeliveryStatus.PENDING)
            #     )
            #     await db.commit()

            # UPDATE USER WALLET
            new_escrow = wallet.escrow_balance + order.delivery.delivery_fee
            new_balance = max(wallet.balance - order.delivery.delivery_fee, 0)
            if new_balance < 0:
                raise HTTPException(
                    status_code=400,
                    detail="Insufficient balance to re-list delivery.",
                )
            await db.execute(
                update(Wallet)
                .where(Wallet.id == current_user.sender_id)
                .values(
                    {
                        "balance": new_balance,
                        "escrow_balance": new_escrow,
                    }
                )
            )
            await db.commit()
            await db.refresh(order.delivery)

            invalidate_delivery_cache(order.delivery.id)
            redis_client.delete(ALL_DELIVERY)
            redis_client.delete("paid_pending_deliveries")
            redis_client.delete(f"user_related_orders:{current_user.id}")
            return DeliveryStatusUpdateSchema(
                delivery_status=order.delivery.delivery_status
            )

        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Something went wrong. {e}",
            )


# For orders without delivery
async def vendor_mark_order_delivered(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    order_result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .where(Order.vendor_id == current_user.id)
    )

    order = order_result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    # vendor_wallet = await fetch_wallet(db, order.vendor_id)

    try:
        if (
            order.vendor_id == current_user.id
            and order.order_status == OrderStatus.DELIVERED
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already mark this order as delivered.",
            )

        if (
            order.vendor_id == current_user.id
            and order.order_status == OrderStatus.PENDING
        ):
            await db.execute(
                update(Order)
                .where(Order.id == order_id)
                .values({"order_status": OrderStatus.DELIVERED})
                .returning(Order.order_status)
            )
            await db.commit()
            await db.refresh(order)

            owner_token = await get_user_notification_token(
                db=db, user_id=order.owner_id
            )

            if owner_token:
                await send_push_notification(
                    tokens=[owner_token],
                    title="Order Delivered",
                    message="Your order has been marked as delivered by the vendor, please verify before marking as received.",
                    navigate_to="/(app)/delivery/orders",
                )


                redis_client.delete(f"delivery:{order_id}")
                redis_client.delete(f"{ALL_DELIVERY}")
                redis_client.delete("paid_pending_deliveries")
                redis_client.delete(f"user_related_orders:{current_user.id}")

                return DeliveryStatusUpdateSchema(order_status=order.order_status)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating status. {e}",
        )


async def rider_accept_delivery_order(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    dispatch_id = get_dispatch_id(current_user)

    result = await db.execute(
        select(Order).where(Order.id == order_id).options(selectinload(Order.delivery)).with_for_update()
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found."
        )

    if order.delivery.rider_phone_number or order.delivery.rider_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This order has been assigned to a rider.",
        )

    if current_user.user_type != UserType.RIDER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a rider can pickup orders. Register a rider",
        )

    if current_user.profile.profile_image.profile_image_url is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile image is missing. Please update your profile",
        )

    if current_user.rider_is_suspended_for_order_cancel:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You have been blocked for too many cancelled order. Wait until your account is reset",
        )

    await db.execute(
        update(Delivery)
        .where(Delivery.id == order.delivery.id)
        .values(
            {
                "delivery_status": DeliveryStatus.ACCEPTED,
                "rider_id": current_user.id,
                "dispatch_id": dispatch_id,
                "rider_phone_number": current_user.profile.phone_number,
            }
        )
        .returning(Delivery.delivery_status)
    )

    await db.commit()

    # Get profile
    dispatch_profile = await get_user_profile(order.delivery.dispatch_id, db=db)
    sender_profile = await get_user_profile(order.owner_id, db=db)

    transaction_result = await db.execute(
        select(Transaction).where(Transaction.tx_ref == order.tx_ref)
    )
    transaction = transaction_result.scalar_one_or_none()

    # Amount to move to escrow
    dispatch_amount = max(order.delivery.amount_due_dispatch, 0)

    # Move dispatch funds to escrow (escrow increases) for dispatch
    # if order.delivery.delivery_type == DeliveryType.PACKAGE:

    await producer.publish_message(
        service="wallet",
        operation="create_transaction",
        payload={
            "wallet_id": str(order.delivery.dispatch_id),
            "tx_ref": str(order.tx_ref),
            "to_wallet_id": str(order.delivery.dispatch_id),
            "amount": str(dispatch_amount),
            "transaction_type": transaction.transaction_type,
            "transaction_direction": TransactionDirection.CREDIT,
            "payment_method": transaction.payment_method,
            "payment_status": transaction.payment_status,
            "from_user": sender_profile.full_name or sender_profile.business_name,
            "to_user": dispatch_profile.full_name or dispatch_profile.business_name,
        },
    )

    # Update sender transaction
    await producer.publish_message(
        service="wallet",
        operation="update_transaction",
        payload={
            "wallet_id": str(order.owner_id),
            "tx_ref": str(order.tx_ref),
            "to_user": dispatch_profile.full_name or dispatch_profile.business_name,
        },
    )

    # Update dispatch escrow
    await producer.publish_message(
        service="wallet",
        operation="update_wallet",
        payload={
            "wallet_id": str(order.delivery.dispatch_id),
            "balance_change":str(0),
            "escrow_change":str(dispatch_amount),
        },
    )


    # Original: Direct DB operations (commented for testing)
    # await db.execute(
    #     update(Wallet)
    #     .where(Wallet.id == order.delivery.dispatch_id)
    #     .values(
    #         {
    #             "escrow_balance": max(
    #                 dispatch_wallet.escrow_balance + dispatch_amount, 0
    #             )
    #         }
    #     )
    # )

    # if (
    #     order.delivery.delivery_type in [DeliveryType.FOOD, DeliveryType.LAUNDRY]
    #     and order.require_delivery == RequireDeliverySchema.DELIVERY
    # ):
    # New: Update escrow using RabbitMQ
    # await wallet_service.publish_wallet_update(
    #     wallet_id=str(order.delivery.dispatch_id),
    #     balance_change=str(0),
    #     escrow_change=str(dispatch_amount),
    #     transaction_type=TransactionType.USER_TO_USER,
    #     transaction_direction=TransactionDirection.CREDIT,
    #     from_user=sender_profile.full_name or sender_profile.business_name,
    #     to_user=dispatch_profile.full_name or dispatch_profile.business_name
    # )

    # await wallet_service.publish_wallet_update(
    #     wallet_id=str(order.delivery.vendor_id),
    #     balance_change=str(0),
    #     escrow_change=str(vendor_amount),
    #     transaction_type=TransactionType.USER_TO_USER,
    #     transaction_direction=TransactionDirection.CREDIT,
    #     from_user=sender_profile.full_name or sender_profile.business_name,
    #     to_user=vendor_profile.full_name or vendor_profile.business_name
    # )

    # Original: Direct DB operations (commented for testing)
    # await db.execute(
    #     update(Wallet)
    #     .where(Wallet.id == order.delivery.dispatch_id)
    #     .values(
    #         {
    #             "escrow_balance": max(
    #                 dispatch_wallet.escrow_balance + dispatch_amount, 0
    #             )
    #         }
    #     )
    # )
    # await db.execute(
    #     update(Wallet)
    #     .where(Wallet.id == order.delivery.vendor_id)
    #     .values(
    #         {"escrow_balance": max(vendor_wallet.escrow_balance + vendor_amount, 0)}
    #     )
    # )

    order.delivery.delivery_status = DeliveryStatus.ACCEPTED
    order.order_status = OrderStatus.ACCEPTED
    await db.commit()
    await db.refresh(order.delivery)

    # redis_client.delete(f"delivery:{delivery_id}")
    redis_client.delete(f"{ALL_DELIVERY}")
    redis_client.delete("paid_pending_deliveries")
    redis_client.delete(f"user_related_orders:{current_user.id}")

    await ws_service.broadcast_delivery_status_update(
        delivery_id=order.delivery.id, new_status=order.delivery.delivery_status
    )

    await ws_service.broadcast_order_status_update(
        order_id=order.id, new_status=order.order_status
    )

    sender_token = await get_user_notification_token(db=db, user_id=order.owner_id)

    if sender_token:
        await send_push_notification(
            tokens=[sender_token],
            title="Order Assigned",
            message=f"Your order has been assigned to {current_user.profile.full_name}, {current_user.profile.phone_number}",
            navigate_to="/(app)/delivery/orders",
        )

    return DeliveryStatusUpdateSchema(delivery_status=order.delivery.delivery_status)


# async def sender_confirm_delivery_or_order_received(
#     db: AsyncSession, order_id: UUID, current_user: User
# ) -> DeliveryStatusUpdateSchema:
#     result = await db.execute(
#         select(Order).where(Order.id == order_id).options(selectinload(Order.delivery))
#     )

#     order = result.scalar_one_or_none()


#     if not order:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
#         )

#     if order.owner_id != current_user.id:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="You are not allowed to perform this action.",
#         )

#     # Handle pickup orders (no delivery)
#     if order.require_delivery == RequireDeliverySchema.PICKUP:
#         if order.order_status != OrderStatus.DELIVERED:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="Order is not yet delivered.",
#             )

#         if order.order_status == OrderStatus.RECEIVED:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="You have already marked this order as received.",
#             )

#         vendor_wallet = await fetch_wallet(db, order.vendor_id)
#         vendor_profile = await get_user_profile(order.vendor_id, db=db)
#         sender = current_user.profile.full_name or current_user.profile.business_name
#         vendor_amount = max(order.amount_due_vendor, 0)
#         return await handle_pickup_order_received(db, order, vendor_wallet, vendor_profile, sender, vendor_amount)

#     # Handle delivery orders
#     if not order.delivery:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="No delivery found for this order.",
#         )

#     if order.delivery.delivery_status != DeliveryStatus.DELIVERED:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Delivery is not yet completed.",
#         )

#     if order.delivery.delivery_status == DeliveryStatus.RECEIVED:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="You have already marked this delivery as received.",
#         )

#     vendor_wallet = await fetch_wallet(db, order.vendor_id)
#     vendor_amount = max(order.amount_due_vendor, 0)


#     dispatch_profile = await get_user_profile(order.delivery.dispatch_id, db=db)
#     vendor_profile = await get_user_profile(order.vendor_id, db=db)
#     sender = current_user.profile.full_name or current_user.profile.business_name

#     try:
#         order.order_status = OrderStatus.RECEIVED
#         order.delivery.delivery_status = DeliveryStatus.RECEIVED
#         await db.commit()

#         if (
#             order.delivery.delivery_type == DeliveryType.PACKAGE
#             and order.require_delivery == RequireDeliverySchema.DELIVERY
#         ):
#             # Update dispatch wallet using RabbitMQ

#             await wallet_service.publish_wallet_update(
#                 wallet_id=str(order.delivery.dispatch_id),
#                 tx_ref=str(order.tx_ref),
#                 balance_change=str(vendor_amount),
#                 escrow_change=str(-vendor_amount),
#                 metadata={
#                     "order_id": str(order.id),
#                     "operation": "package_delivery_escrow",
#                     "is_new_transaction": False,
#                 }
#             )
#             await wallet_service.publish_wallet_update(
#                 wallet_id=str(order.owner_id),
#                 tx_ref=str(order.tx_ref),
#                 balance_change=str(0),
#                 escrow_change=str(-order.delivery.delivery_fee),
#                 metadata={
#                     "order_id": str(order.id),
#                     "operation": "package_delivery_escrow",
#                     "is_new_transaction": False,
#                 }
#             )


#             # await wallet_service.publish_wallet_update(
#             #     wallet_id=str(order.delivery.dispatch_id),
#             #     balance_change=str(order.delivery.amount_due_dispatch),
#             #     escrow_change=str(-order.delivery.amount_due_dispatch),
#             #     transaction_type=TransactionType.USER_TO_USER,
#             #     transaction_direction=TransactionDirection.CREDIT,
#             #     from_user=sender,
#             #     to_user=dispatch_profile.full_name or dispatch_profile.business_name
#             # )

#             # # New: Update sender wallet using RabbitMQ
#             # await wallet_service.publish_wallet_update(
#             #     wallet_id=str(order.delivery.sender_id),
#             #     balance_change=str(0),
#             #     escrow_change=str(-order.delivery.delivery_fee),
#             #     transaction_type=TransactionType.USER_TO_USER,
#             #     transaction_direction=TransactionDirection.DEBIT,
#             #     from_user=sender,
#             #     to_user=dispatch_profile.full_name or dispatch_profile.business_name
#             # )

#             # Original: Direct DB operations (commented for testing)
#             # await db.execute(
#             #     update(Wallet)
#             #     .where(Wallet.id == order.delivery.dispatch_id)
#             #     .values(
#             #         balance=Wallet.balance + order.delivery.amount_due_dispatch,
#             #         escrow_balance=Wallet.escrow_balance
#             #         - order.delivery.amount_due_dispatch,
#             #     )
#             # )
#             # await db.execute(
#             #     update(Wallet)
#             #     .where(Wallet.id == order.delivery.sender_id)
#             #     .values(
#             #         escrow_balance=Wallet.escrow_balance - order.delivery.delivery_fee
#             #     )
#             # )

#             order.delivery.delivery_status = DeliveryStatus.RECEIVED
#             await db.commit()
#             await db.refresh(order)

#             # create transaction for dispatch
#             # await create_wallet_transaction(
#             #     db=db,
#             #     wallet_id=order.delivery.dispatch_id,
#             #     amount=order.delivery.amount_due_dispatch,
#             #     transaction_direction=TransactionDirection.CREDIT,
#             #     transaction_type=TransactionType.USER_TO_USER,
#             #     from_user=sender,
#             #     to_user=dispatch_profile.full_name or dispatch_profile.business_name,
#             # )

#             await ws_service.broadcast_delivery_status_update(
#                 delivery_id=order.delivery.id, new_status=order.delivery.delivery_status
#             )

#         if (
#             order.require_delivery == RequireDeliverySchema.DELIVERY
#             and order.delivery.delivery_type
#             in [DeliveryType.FOOD, DeliveryType.LAUNDRY]
#         ):
#             # New: Update wallets using RabbitMQ
#             await wallet_service.publish_wallet_update(
#                 wallet_id=order.delivery.dispatch_id,
#                 balance_change=order.delivery.amount_due_dispatch,
#                 escrow_change=-order.delivery.amount_due_dispatch,
#                 transaction_type=TransactionType.USER_TO_USER,
#                 transaction_direction=TransactionDirection.CREDIT,
#                 from_user=sender,
#                 to_user=dispatch_profile.full_name or dispatch_profile.business_name
#             )

#             await wallet_service.publish_wallet_update(
#                 wallet_id=order.vendor_id,
#                 balance_change=order.amount_due_vendor,
#                 escrow_change=-order.amount_due_vendor,
#                 transaction_type=TransactionType.USER_TO_USER,
#                 transaction_direction=TransactionDirection.CREDIT,
#                 from_user=sender,
#                 to_user=vendor_profile.full_name or vendor_profile.business_name
#             )

#             total_spent = order.total_price + order.delivery.delivery_fee
#             await wallet_service.publish_wallet_update(
#                 wallet_id=order.owner_id,
#                 balance_change=0,
#                 escrow_change=-total_spent,
#                 transaction_type=TransactionType.USER_TO_USER,
#                 transaction_direction=TransactionDirection.DEBIT,
#                 from_user=sender,
#                 to_user=vendor_profile.full_name or vendor_profile.business_name
#             )

#             # Original: Direct DB operations (commented for testing)
#             # await db.execute(
#             #     update(Wallet)
#             #     .where(Wallet.id == order.delivery.dispatch_id)
#             #     .values(
#             #         balance=Wallet.balance + order.delivery.amount_due_dispatch,
#             #         escrow_balance=Wallet.escrow_balance
#             #         - order.delivery.amount_due_dispatch,
#             #     )
#             # )
#             # await db.execute(
#             #     update(Wallet)
#             #     .where(Wallet.id == order.vendor_id)
#             #     .values(
#             #         balance=Wallet.balance + order.amount_due_vendor,
#             #         escrow_balance=Wallet.escrow_balance - order.amount_due_vendor,
#             #     )
#             # )
#             # total_spent = order.total_price + order.delivery.delivery_fee
#             # await db.execute(
#             #     update(Wallet)
#             #     .where(Wallet.id == order.owner_id)
#             #     .values(escrow_balance=Wallet.escrow_balance - total_spent)
#             # )

#             order.order_status = OrderStatus.RECEIVED

#             await db.commit()
#             await db.refresh(order)

#             # Create credit transactions for dispatch and vendor
#             # await create_wallet_transaction(
#             #     db=db,
#             #     wallet_id=order.delivery.dispatch_id,
#             #     amount=order.delivery.amount_due_dispatch,
#             #     transaction_direction=TransactionDirection.CREDIT,
#             #     transaction_type=TransactionType.USER_TO_USER,
#             #     from_user=sender,
#             #     to_user=dispatch_profile.full_name or dispatch_profile.business_name,
#             # )

#             # # create vendor transaction
#             # await create_wallet_transaction(
#             #     db=db,
#             #     wallet_id=vendor_wallet.id,
#             #     amount=vendor_amount,
#             #     transaction_direction=TransactionDirection.CREDIT,
#             #     transaction_type=TransactionType.USER_TO_USER,
#             #     from_user=sender,
#             #     to_user=vendor_profile.full_name
#             #     if vendor_profile.full_name
#             #     else vendor_profile.business_name,
#             # )

#             await ws_service.broadcast_delivery_status_update(
#                 delivery_id=order.delivery.id, new_status=order.delivery.delivery_status
#             )
#             await ws_service.broadcast_order_status_update(
#                 order_id=order.id, new_status=order.order_status
#             )

#         if (
#             order.require_delivery == RequireDeliverySchema.PICKUP
#             and order.order_type in [OrderType.FOOD, OrderType.LAUNDRY]
#         ):
#             # New: Update wallets using RabbitMQ
#             await wallet_service.publish_wallet_update(
#                 wallet_id=order.vendor_id,
#                 balance_change=order.amount_due_vendor,
#                 escrow_change=-order.amount_due_vendor,
#                 transaction_type=TransactionType.USER_TO_USER,
#                 transaction_direction=TransactionDirection.CREDIT,
#                 from_user=sender,
#                 to_user=vendor_profile.full_name or vendor_profile.business_name
#             )

#             await wallet_service.publish_wallet_update(
#                 wallet_id=order.owner_id,
#                 balance_change=0,
#                 escrow_change=-order.total_price,
#                 transaction_type=TransactionType.USER_TO_USER,
#                 transaction_direction=TransactionDirection.DEBIT,
#                 from_user=sender,
#                 to_user=vendor_profile.full_name or vendor_profile.business_name
#             )

#             # Original: Direct DB operations (commented for testing)
#             # await db.execute(
#             #     update(Wallet)
#             #     .where(Wallet.id == order.vendor_id)
#             #     .values(
#             #         balance=Wallet.balance + order.amount_due_vendor,
#             #         escrow_balance=Wallet.escrow_balance - order.amount_due_vendor,
#             #     )
#             # )
#             # await db.execute(
#             #     update(Wallet)
#             #     .where(Wallet.id == order.owner_id)
#             #     .values(escrow_balance=Wallet.escrow_balance - order.total_price)
#             # )

#             order.delivery.order.order_status = OrderStatus.RECEIVED

#             await db.commit()
#             await db.refresh(order)

#             # create vendor transaction
#             # await create_wallet_transaction(
#             #     db=db,
#             #     wallet_id=vendor_wallet.id,
#             #     amount=vendor_amount,
#             #     transaction_direction=TransactionDirection.CREDIT,
#             #     transaction_type=TransactionType.USER_TO_USER,
#             #     from_user=sender,
#             #     to_user=vendor_profile.full_name
#             #     if vendor_profile.full_name
#             #     else vendor_profile.business_name,
#             # )

#             await ws_service.broadcast_order_status_update(
#                 order_id=order.id, new_status=order.order_status
#             )

#         rider_token = await get_user_notification_token(
#             db=db, user_id=order.delivery.rider_id
#         )
#         vendor_token = await get_user_notification_token(db=db, user_id=order.vendor_id)


#         if rider_token:
#             await notification_queue.publish_notification(
#                  tokens=[rider_token],
#                 title="Order completed",
#                 message=f"Congratulations! Order completed. {order.delivery.amount_due_dispatch} has been released to your wallet",
#                 navigate_to="/(app)/delivery/orders",
#             )


#         if vendor_token:
#             await notification_queue.publish_notification(
#                 tokens=[rider_token],
#                 title="Order completed",
#                 message=f"Congratulations! Order completed. {order.delivery.amount_due_dispatch} has been released to your wallet",
#                 navigate_to="/(app)/delivery/orders",
#             )
#         # if rider_token:
#         #     await send_push_notification(
#         #         tokens=[rider_token],
#         #         title="Order completed",
#         #         message=f"Congratulations! Order completed. {order.delivery.amount_due_dispatch} has been released to your wallet",
#         #         navigate_to="/(app)/delivery/orders",
#         #     )
#         # if vendor_token:
#         #     await send_push_notification(
#         #         tokens=[vendor_token],
#         #         title="Order completed",
#         #         message=f"Congratulations! Order completed. ₦{order.delivery.order.amount_due_vendor} has been credited to your wallet",
#         #         navigate_to="/(app)/delivery/orders",
#         #     )


#         # redis_client.delete(f"delivery:{delivery_id}")
#         redis_client.delete(f"{ALL_DELIVERY}")
#         redis_client.delete("paid_pending_deliveries")
#         redis_client.delete(f"user_related_orders:{current_user.id}")

#         return DeliveryStatusUpdateSchema(
#             delivery_status=order.delivery.delivery_status,
#             order_status=order.order_status,
#         )

#     except Exception as e:
#         await db.rollback()
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def sender_confirm_delivery_or_order_received(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    result = await db.execute(
        select(Order).where(Order.id == order_id).options(selectinload(Order.delivery)).with_for_update()
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )

    if order.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not allowed to perform this action.",
        )

    # Ensure tx_ref exists
    if not order.tx_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transaction reference found for this order.",
        )

    # Handle pickup orders (no delivery)
    if order.require_delivery == RequireDeliverySchema.PICKUP:
        if order.order_status != OrderStatus.DELIVERED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order is not yet delivered.",
            )

        if order.order_status == OrderStatus.RECEIVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already marked this order as received.",
            )

        vendor_profile = await get_user_profile(order.vendor_id, db=db)

    # Handle delivery orders
    if not order.delivery:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No delivery found for this order.",
        )

    if order.delivery.delivery_status != DeliveryStatus.DELIVERED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Delivery is not yet completed.",
        )

    if order.delivery.delivery_status == DeliveryStatus.RECEIVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already marked this delivery as received.",
        )

    dispatch_profile = await get_user_profile(order.delivery.dispatch_id, db=db)
    vendor_profile = await get_user_profile(order.vendor_id, db=db)

    total_spent = order.total_price + order.delivery.delivery_fee

    to_user = (
        f"{dispatch_profile.full_name or dispatch_profile.business_name} - {vendor_profile.full_name or vendor_profile.business_name}"
        if order.delivery
        else vendor_profile.full_name or vendor_profile.business_name
    )
    try:
        order.order_status = OrderStatus.RECEIVED
        order.delivery.delivery_status = DeliveryStatus.RECEIVED
        tx_ref = order.tx_ref

        await db.commit()
        await db.refresh(order)

        await ws_service.broadcast_delivery_status_update(
            delivery_id=order.delivery.id, new_status=order.delivery.delivery_status
        )
        await ws_service.broadcast_order_status_update(
            order_id=order.id, new_status=order.order_status
        )

        rider_token = await get_user_notification_token(
            db=db, user_id=order.delivery.rider_id
        )
        vendor_token = await get_user_notification_token(db=db, user_id=order.vendor_id)

        if rider_token:
            await send_push_notification(
                tokens=[rider_token],
                title="Order Completed",
                message=f"Congratulations! Order completed. ₦{order.delivery.amount_due_dispatch} has been released to your wallet.",
                navigate_to="/(app)/delivery/orders",
            )

        if vendor_token:
            await send_push_notification(
                tokens=[vendor_token],
                title="Order Completed",
                message=f"Congratulations! Order completed. ₦{order.amount_due_vendor} has been released to your wallet.",
                navigate_to="/(app)/delivery/orders",
            )

        redis_client.delete(f"{ALL_DELIVERY}")
        redis_client.delete("paid_pending_deliveries")
        redis_client.delete(f"user_related_orders:{current_user.id}")

        # Update dispatch wallet (move from escrow to balance)
        if order.delivery and order.delivery.delivery_fee > 0:
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.delivery.dispatch_id),
                    "balance_change": str(order.delivery.amount_due_dispatch),
                    "escrow_change": str(-order.delivery.amount_due_dispatch),
                },
            )

        # Update vendor wallet (move from escrow to balance)
        await producer.publish_message(
            service="wallet",
            operation="update_wallet",
            payload={
                "wallet_id": str(order.vendor_id),
                "tx_ref": str(tx_ref),
                "balance_change": str(order.amount_due_vendor),
                "escrow_change": str(-order.amount_due_vendor),
            },
        )
        # Update sender wallet (move from escrow)
        await producer.publish_message(
            service="wallet",
            operation="update_wallet",
            payload={
                "wallet_id": str(order.owner_id),
                "tx_ref": str(tx_ref),
                "balance_change": str(0),
                "escrow_change": str(-total_spent),
            },
        )

        # Update sender transaction
        await producer.publish_message(
            service="wallet",
            operation="update_transaction",
            payload={
                "wallet_id": str(order.owner_id),
                "tx_ref": str(order.tx_ref),
                "to_user": to_user,
            },
        )


        return DeliveryStatusUpdateSchema(
            delivery_status=order.delivery.delivery_status,
            order_status=order.order_status,
        )

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def vendor_mark_laundry_item_received(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Allow a laundry vendor to mark a laundry item as received.
    This function handles the transition and wallet updates when a laundry vendor receives items.
    """
    # Fetch order with delivery info
    result = await db.execute(
        select(Order).where(Order.id == order_id).options(selectinload(Order.delivery)).with_for_update()
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )

    if not order.delivery:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This order does not have an associated delivery.",
        )

    # Verify user is authorized
    if current_user.id != order.vendor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to mark this order as received.",
        )

    if current_user.user_type not in [
        UserType.LAUNDRY_VENDOR,
        UserType.RESTAURANT_VENDOR,
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only vendors can mark laundry items as received.",
        )

    # Check delivery status
    if order.delivery.delivery_status != DeliveryStatus.DELIVERED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order must be delivered before it can be marked as received.",
        )

    if order.delivery.delivery_status == DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This order has already been marked as received.",
        )

    # Ensure tx_ref exists
    if not order.tx_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transaction reference found for this order.",
        )

    try:
        # Fetch profiles for transaction and notification records
        sender_profile = await get_user_profile(order.owner_id, db=db)
        dispatch_profile = await get_user_profile(order.delivery.dispatch_id, db=db)

        sender_token = await get_user_notification_token(db=db, user_id=order.delivery.sender_id)
        rider_token = await get_user_notification_token(db=db, user_id=order.delivery.rider_id)

        # Prepare notification data

        notification_data = []

        if rider_token:
            notification_data.append({"tokens": [rider_token],
                "title": "Payment Completed",
                "message": f"Delivery completed. ₦{order.delivery.amount_due_dispatch} has been credited to your wallet.",
                "navigate_to": "/(app)/delivery/orders",
            })
        if sender_token:
            notification_data.append(
            {
                "tokens": [sender_token],
                "title": "Laundry Items Received",
                "message": "Your laundry items have been received by the vendor.",
                "navigate_to": "/(app)/delivery/orders",
            })

        # Prepare cache keys to clear
        cache_keys = [
            f"{ALL_DELIVERY}",
            "paid_pending_deliveries",
            f"user_related_orders:{current_user.id}",
            f"user_related_orders:{order.owner_id}",
            f"user_related_orders:{order.delivery.rider_id}",
        ]

        # Queue order and delivery status update
        await producer.publish_message(
            service='order_status',
            operation='update_order_status',
            payload={
                'order_id': str(order.id),
                'delivery_id': str(order.delivery.dispatch_id),
                'order_status': OrderStatus.VENDOR_RECEIVED_LAUNDRY_ITEM,
                'delivery_status': DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM,
                'cache_keys': cache_keys,
                'notification_data': notification_data,
               
            }
            
        )

        # Update dispatch wallet (move from escrow to balance)
        await producer.publish_message(
            service='wallet',
            operation='update_wallet',
            payload={
                'wallet_id':str(order.delivery.dispatch_id),
                'balance_change':str(order.delivery.amount_due_dispatch),
                'escrow_change':str(-order.delivery.amount_due_dispatch),
                

        })
       

        # Update sender's wallet (clear escrow for delivery fee)
        await producer.publish_message( 
            service='wallet',
            operation='update_wallet',
            payload= {
                'wallet_id':str(order.owner_id),
                'balance_change':str(0),
                'escrow_change':str(-order.delivery.delivery_fee),
          })

        # Broadcast updates (optional, as consumer might handle this, but kept for immediate feedback)
        await ws_service.broadcast_delivery_status_update(
            delivery_id=order.delivery.id,
            new_status=DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM,
        )

        await ws_service.broadcast_order_status_update(
            order_id=order.id, new_status=OrderStatus.VENDOR_RECEIVED_LAUNDRY_ITEM
        )

        return DeliveryStatusUpdateSchema(
            delivery_status=DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM,
            order_status=OrderStatus.VENDOR_RECEIVED_LAUNDRY_ITEM,
        )

    except Exception as e:
        # No db.rollback() needed since no direct DB changes
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process laundry item receipt: {str(e)}",
        )


# async def vendor_mark_laundry_item_received(
#     db: AsyncSession, order_id: UUID, current_user: User
# ) -> DeliveryStatusUpdateSchema:
#     """
#     Allow a laundry vendor to mark a laundry item as received.
#     This function handles the transition and wallet updates when a laundry vendor receives items.
#     """
#     # Fetch order with delivery info
#     result = await db.execute(
#         select(Order)
#         .where(Order.id == order_id)
#         .options(selectinload(Order.delivery))
#     )
#     order = result.scalar_one_or_none()

#     if not order:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
#         )

#     if not order.delivery:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="This order does not have an associated delivery."
#         )

#     # Verify user is authorized
#     if current_user.id != order.vendor_id:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="You are not authorized to mark this order as received."
#         )

#     if current_user.user_type not in [UserType.LAUNDRY_VENDOR, UserType.RESTAURANT_VENDOR]:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Only vendors can mark laundry items as received."
#         )

#     # Check delivery status
#     if order.delivery.delivery_status != DeliveryStatus.DELIVERED:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="Order must be delivered before it can be marked as received."
#         )

#     if order.delivery.delivery_status == DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="This order has already been marked as received."
#         )

#     try:
#         # Fetch profiles for transaction records
#         sender_profile = await get_user_profile(order.delivery.sender_id, db=db)
#         dispatch_profile = await get_user_profile(order.delivery.dispatch_id, db=db)

#         # Update delivery and order status
#         await db.execute(
#             update(Delivery)
#             .where(Delivery.id == order.delivery.id)
#             .values(delivery_status=DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM)
#         )

#         await db.execute(
#             update(Order)
#             .where(Order.id == order_id)
#             .values(order_status=OrderStatus.VENDOR_RECEIVED_LAUNDRY_ITEM)
#         )

#         try:
#             # Update dispatch wallet safely
#             await safe_wallet_update(
#                 db=db,
#                 wallet_id=order.delivery.dispatch_id,
#                 balance_change=order.delivery.amount_due_dispatch,  # Increase balance
#                 escrow_change=-order.delivery.amount_due_dispatch,  # Decrease escrow
#             )

#             # Update sender's escrow safely
#             await safe_wallet_update(
#                 db=db,
#                 wallet_id=order.delivery.sender_id,
#                 escrow_change=-order.delivery.delivery_fee,  # Decrease escrow
#             )

#             # Queue transaction record for async processing
#             await queue_wallet_transaction(
#                 db=db,
#                 wallet_id=order.delivery.dispatch_id,
#                 amount=order.delivery.amount_due_dispatch,
#                 transaction_type=TransactionType.USER_TO_USER,
#                 transaction_direction=TransactionDirection.CREDIT,
#                 from_user=sender_profile.full_name if sender_profile.full_name else sender_profile.business_name,
#                 to_user=dispatch_profile.full_name if dispatch_profile.full_name else dispatch_profile.business_name,
#             )
#         except WalletUpdateError as e:
#             # Log the error but don't fail the whole operation
#             logger_config.error(f"Wallet update failed: {str(e)}")
#             # Consider sending an alert to admin

#         await db.commit()
#         await db.refresh(order)

#         # Send notifications
#         sender_token = await get_user_notification_token(db=db, user_id=order.delivery.sender_id)
#         rider_token = await get_user_notification_token(db=db, user_id=order.delivery.rider_id)

#         if sender_token:
#             await notification_queue.publish_notification(
#                 tokens=[sender_token],
#                 title="Laundry Items Received",
#                 message="Your laundry items have been received by the vendor",
#                 navigate_to="/(app)/delivery/orders",
#             )

#         if rider_token:
#             await notification_queue.publish_notification(
#                 tokens=[rider_token],
#                 title="Payment Completed",
#                 message=f"Delivery completed. ₦{order.delivery.amount_due_dispatch} has been credited to your wallet",
#                 navigate_to="/(app)/delivery/orders",
#             )

#         # if sender_token:
#         #     await send_push_notification(
#         #         tokens=[sender_token],
#         #         title="Laundry Items Received",
#         #         message="Your laundry items have been received by the vendor",
#         #         navigate_to="/(app)/delivery/orders",
#         #     )

#         # if rider_token:
#         #     await send_push_notification(
#         #         tokens=[rider_token],
#         #         title="Payment Completed",
#         #         message=f"Delivery completed. ₦{order.delivery.amount_due_dispatch} has been credited to your wallet",
#         #         navigate_to="/(app)/delivery/orders",
#         #     )

#         # Clear caches
#         redis_client.delete(f"{ALL_DELIVERY}")
#         redis_client.delete("paid_pending_deliveries")
#         redis_client.delete(f"user_related_orders:{current_user.id}")
#         redis_client.delete(f"user_related_orders:{order.delivery.sender_id}")
#         redis_client.delete(f"user_related_orders:{order.delivery.rider_id}")

#         # Broadcast updates
#         await ws_service.broadcast_delivery_status_update(
#             delivery_id=order.delivery.id,
#             delivery_status=DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM
#         )

#         await ws_service.broadcast_order_status_update(
#             delivery_id=order.id,
#             new_status=OrderStatus.VENDOR_RECEIVED_LAUNDRY_ITEM
#         )

#         return DeliveryStatusUpdateSchema(
#             delivery_status=DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM,
#             order_status=OrderStatus.VENDOR_RECEIVED_LAUNDRY_ITEM,
#         )

#     except Exception as e:
#         await db.rollback()
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to process laundry item receipt: {str(e)}"
#         )


async def rider_mark_delivered(
    delivery_id: UUID, current_user: User, db: AsyncSession
) -> DeliveryStatusUpdateSchema:
    result = await db.execute(
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .where(
            or_(
                Delivery.rider_id == current_user.id,
                Delivery.dispatch_id == current_user.id,
            )
        )
        .options(selectinload(Delivery.order))
        .with_for_update()
    )

    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found.")
    if current_user.user_type not in [UserType.RIDER, UserType.DISPATCH] and (
        delivery.rider_id != current_user.id or delivery.dispatch_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="You are not allowed to perform this action.",
        )

    if delivery.delivery_status == DeliveryStatus.ACCEPTED:
        await db.execute(
            update(Delivery)
            .where(Delivery.id == delivery_id)
            .values({"delivery_status": DeliveryStatus.DELIVERED})
        )

        delivery.order.order_status = OrderStatus.DELIVERED
        await db.commit()
        await db.refresh(delivery)

    token = await get_user_notification_token(db=db, user_id=delivery.sender_id)

    if token:
        await send_push_notification(
            tokens=[token],
            title="Payment Successful",
            message="Your order has been delivered. Please confirm with the receipient before marking as received.",
            navigate_to="/(app)/delivery",
        )

    # if token:
    #     await send_push_notification(
    #         tokens=[token],
    #         title="Order Delivered",
    #         message="Your order has been delivered. Please confirm with the receipient before marking as received.",
    #         navigate_to="/(app)/delivery",
    #     )

    redis_client.delete(f"delivery:{delivery_id}")
    redis_client.delete(ALL_DELIVERY)
    redis_client.delete("paid_pending_deliveries")
    redis_client.delete(f"user_related_orders:{current_user.id}")

    await ws_service.broadcast_delivery_status_update(
        delivery_id=delivery.id, new_status=delivery.delivery_status
    )
    await ws_service.broadcast_delivery_status_update(
        delivery_id=delivery.id, new_status=delivery.order.order_status
    )

    return DeliveryStatusUpdateSchema(
        delivery_status=delivery.delivery_status,
        order_status=delivery.order.order_status,
    )


# <<<--- admin_modify_delivery_status --->>>
async def admin_modify_delivery_status(
    db: AsyncSession, delivery_id: UUID, new_status: DeliveryStatus, current_user: dict
) -> DeliveryResponse:
    """
    Allows an ADMIN user to forcibly change the status of any delivery.

    Args:
        db: Async database session.
        delivery_id: The UUID of the delivery to modify.
        new_status: The new DeliveryStatus enum value to set.
        current_user: The user performing the action (must be ADMIN).

    Returns:
        The updated DeliveryResponse object.

    Raises:
        HTTPException: If user is not ADMIN, delivery not found, or update fails.
    """
    if current_user.get("user_type") != UserType.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied. Only ADMIN users can modify delivery status directly.",
        )

    try:
        result = await db.execute(select(Delivery).where(Delivery.id == delivery_id).with_for_update())
        delivery = result.scalar_one_or_none()

        if not delivery:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Delivery {delivery_id} not found.",
            )

        old_status = delivery.delivery_status
        # Update delivery status
        await db.execute(
            update(Delivery)
            .where(Delivery.id == delivery_id)
            .values(delivery_status=DeliveryStatus.RECEIVED)
        )

        await db.commit()
        await db.refresh(delivery)

        # --- AUDIT LOG ---
        audit = AuditLog(
            actor_id=current_user.get("id"),
            actor_name=current_user.get("email", "unknown"),
            actor_role=str(current_user.get("user_type", "unknown")),
            action="admin_modify_delivery_status",
            resource_type="Delivery",
            resource_id=delivery_id,
            resource_summary=f"Admin changed delivery status for {delivery_id}",
            changes={"delivery_status": [str(old_status), str(new_status)]},
            extra_metadata=None,
        )
        db.add(audit)
        await db.commit()

        invalidate_delivery_cache(delivery_id)
        redis_client.delete("all_deliveries")

        redis_client.delete(f"delivery:{delivery_id}")
        redis_client.delete(ALL_DELIVERY)
        redis_client.delete("paid_pending_deliveries")
        redis_client.delete(f"user_related_orders:{current_user.id}")

        await ws_service.broadcast_delivery_status_update(
            delivery_id=delivery.id, delivery_status=delivery.delivery_status
        )

        return DeliveryStatusUpdateSchema(delivery_status=new_status)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update delivery status: {e}",
        )


# <<< ----- UTILITY FUNCTIONS FOR ORDERS/DELIVERY ----- >>>

# async def handle_pickup_order_received(
#     db: AsyncSession,
#     order: Order,
#     vendor_profile: Profile,
#     sender: str,
#     vendor_amount: Decimal,
# ) -> DeliveryStatusUpdateSchema:
#     """Helper function to handle pickup order received confirmation"""
#     try:
#         # Update order status
#         order.order_status = OrderStatus.RECEIVED
#         tx_ref = order.tx_ref  # Use existing tx_ref from payment

#         # Update vendor wallet (move from escrow to balance)
#         await wallet_service.publish_wallet_update(
#             wallet_id=str(order.vendor_id),
#             tx_ref=tx_ref,
#             balance_change=str(vendor_amount),
#             escrow_change=str(-vendor_amount),
#             transaction_type=TransactionType.USER_TO_USER,
#             transaction_direction=TransactionDirection.CREDIT,
#             payment_status=PaymentStatus.PAID,
#             from_user=sender,
#             to_user=vendor_profile.full_name or vendor_profile.business_name,
#             metadata={
#                 "order_id": str(order.id),
#                 "operation": "pickup_order_escrow_release",
#                 "is_new_transaction": False,
#             },
#         )

#         # Update customer wallet (clear escrow)
#         await wallet_service.publish_wallet_update(
#             wallet_id=str(order.owner_id),
#             tx_ref=tx_ref,
#             balance_change=str(0),
#             escrow_change=str(-order.total_price),
#             transaction_type=TransactionType.USER_TO_USER,
#             transaction_direction=TransactionDirection.DEBIT,
#             payment_status=PaymentStatus.PAID,
#             from_user=sender,
#             to_user=vendor_profile.full_name or vendor_profile.business_name,
#             metadata={
#                 "order_id": str(order.id),
#                 "operation": "pickup_order_escrow_release",
#                 "is_new_transaction": False,
#             },
#         )

#         await db.commit()
#         await db.refresh(order)

#         # Send notification to vendor
#         vendor_token = await get_user_notification_token(db=db, user_id=order.vendor_id)
#         if vendor_token:
#             await notification_queue.publish_notification(
#                 tokens=[vendor_token],
#                 title="Order Completed",
#                 message=f"Congratulations! Order completed. ₦{vendor_amount} has been released to your wallet.",
#                 navigate_to="/(app)/delivery/orders",
#             )

#         # Clear cache
#         redis_client.delete(f"{ALL_DELIVERY}")
#         redis_client.delete("paid_pending_deliveries")
#         redis_client.delete(f"user_related_orders:{order.owner_id}")
#         redis_client.delete(f"user_related_orders:{order.vendor_id}")

#         # Broadcast status update
#         await ws_service.broadcast_order_status_update(
#             order_id=order.id, new_status=order.order_status
#         )

#         return DeliveryStatusUpdateSchema(order_status=order.order_status)

#     except Exception as e:
#         await db.rollback()
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def get_charges(db: AsyncSession):
    result = await db.execute(select(ChargeAndCommission))
    charge = result.scalars().first()
    return charge


async def calculate_delivery_fee(distance: Decimal, db: AsyncSession) -> Decimal:
    delivery_fee = await get_charges(db)

    if distance <= 1:
        return delivery_fee.base_delivery_fee + delivery_fee.delivery_fee_per_km

    return (
        distance * delivery_fee.delivery_fee_per_km
    ) + delivery_fee.base_delivery_fee


async def calculate_amount_due_dispatch(
    db: AsyncSession, delivery_fee: Decimal
) -> Decimal:
    _delivery_commission = await get_charges(db)

    return delivery_fee - (
        delivery_fee * _delivery_commission.delivery_commission_percentage
    )


async def calculate_amount_due_vendor(
    db: AsyncSession, order_items: list[OrderItemCreate]
) -> Decimal:
    total_price = Decimal("0.00")
    for item in order_items:
        # fetch each item price (meal or linen service)
        result = await db.execute(select(Item).where(Item.id == item.item_id))
        item_data = result.scalar_one_or_none()

        if not item_data:
            raise Exception("Invalid item selected")

        total_price += Decimal(item_data.price) * item.quantity

    # 3. Calculate commission
    # Fetch commission config
    charge = await get_charges(db)
    return total_price - (total_price * charge.food_laundry_commission_percentage)


async def fetch_wallet(db: AsyncSession, user_id: UUID) -> WalletRespose:
    """Fetches a wallet for a user."""
    result = await db.execute(select(Wallet).where(Wallet.id == user_id))
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found"
        )
    return wallet


async def create_wallet_transaction(
    db: AsyncSession,
    wallet_id: UUID,
    amount: Decimal,
    transaction_type: TransactionType,
    transaction_direction: TransactionDirection,
    to_user: str = None,
    from_user: str = None,
) -> Transaction:
    """Creates a wallet transaction."""

    transx = Transaction(
        wallet_id=wallet_id,
        amount=amount,
        payment_status=PaymentStatus.PAID,
        transaction_type=transaction_type,
        transaction_direction=transaction_direction,
        to_user=to_user,
        from_user=from_user,
    )
    db.add(transx)
    await db.commit()
    await db.refresh(transx)
    return transx


class WalletUpdateError(Exception):
    """Custom exception for wallet update errors"""

    pass


async def safe_wallet_update(
    db: AsyncSession,
    wallet_id: UUID,
    balance_change: Decimal = Decimal("0"),
    escrow_change: Decimal = Decimal("0"),
    allow_negative: bool = False,
) -> tuple[Decimal, Decimal]:
    """
    Safely update wallet balances ensuring they never go negative unless explicitly allowed.

    Args:
        db: Database session
        wallet_id: Wallet ID to update
        balance_change: Amount to change balance by (positive for increase, negative for decrease)
        escrow_change: Amount to change escrow by (positive for increase, negative for decrease)
        allow_negative: Whether to allow negative balances (default False)

    Returns:
        tuple[Decimal, Decimal]: New balance and new escrow balance

    Raises:
        WalletUpdateError: If update would result in negative balance/escrow and not allowed
    """
    # Fetch current wallet state
    result = await db.execute(select(Wallet).where(Wallet.id == wallet_id))
    wallet = result.scalar_one_or_none()

    if not wallet:
        raise WalletUpdateError(f"Wallet {wallet_id} not found")

    # Calculate new values
    new_balance = wallet.balance + balance_change
    new_escrow = wallet.escrow_balance + escrow_change

    # Check for negative values if not allowed
    if not allow_negative:
        if new_balance < 0:
            raise WalletUpdateError(
                f"Insufficient balance: {wallet.balance} available, {abs(balance_change)} needed"
            )
        if new_escrow < 0:
            raise WalletUpdateError(
                f"Insufficient escrow: {wallet.escrow_balance} available, {abs(escrow_change)} needed"
            )

    # Ensure we never go below zero even if negative is allowed
    new_balance = max(new_balance, Decimal("0"))
    new_escrow = max(new_escrow, Decimal("0"))

    # Update wallet
    await db.execute(
        update(Wallet)
        .where(Wallet.id == wallet_id)
        .values(
            balance=new_balance,
            escrow_balance=new_escrow,
        )
    )

    return new_balance, new_escrow


async def update_delivery_status_in_db(
    db: AsyncSession, delivery_id: UUID, _status: DeliveryStatus
) -> dict:
    """Updates the delivery status in the database."""
    result = await db.execute(
        update(Delivery)
        .where(Delivery.id == delivery_id)
        .values(delivery_status=_status)
    )
    await db.commit()
    updated_status = result.scalar_one_or_none()
    return updated_status


async def fetch_delivery_by_id(db: AsyncSession, delivery_id: UUID) -> DeliveryResponse:
    """Fetches a delivery by its ID."""
    result = await db.execute(select(Delivery).where(Delivery.id == delivery_id))
    delivery = result.scalar_one_or_none()
    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found"
        )
    return delivery


def format_delivery_response(
    order: Order, delivery: Optional[Delivery] = None
) -> DeliveryResponse:
    # Format order items with proper image structure

    order_items = []
    for order_item in order.order_items:
        item = order_item.item
        images = [
            {"id": image.id, "item_id": image.item_id, "url": image.url}
            for image in item.images
        ]

        order_items.append(
            {
                "id": item.id,
                "user_id": item.user_id,
                "name": item.name,
                "price": item.price,
                "images": images,
                "description": item.description or "",
                "quantity": order_item.quantity,
            }
        )

    # Format delivery if exists
    delivery_data = None
    if delivery:
        delivery_data = {
            "id": str(delivery.id),
            "delivery_type": delivery.delivery_type.value,
            "delivery_status": delivery.delivery_status.value,
            "sender_id": delivery.sender_id,
            "vendor_id": delivery.vendor_id,
            "rider_id": delivery.rider_id,
            "dispatch_id": delivery.dispatch_id if delivery.dispatch_id else None,
            "distance": delivery.distance,
            "delivery_fee": delivery.delivery_fee,
            "amount_due_dispatch": delivery.amount_due_dispatch,
            "pickup_coordinates": delivery.pickup_coordinates,
            "dropoff_coordinates": delivery.dropoff_coordinates,
            "origin": delivery.origin,
            "destination": delivery.destination,
            "duration": delivery.duration,
            "created_at": delivery.created_at.isoformat(),
            "rider_phone_number": delivery.rider_phone_number,
            "sender_phone_number": delivery.sender_phone_number,
        }

    # Format order
    order_data = {
        "id": str(order.id),
        "user_id": str(order.owner_id),
        "order_number": order.order_number,
        "vendor_id": str(order.vendor_id),
        "business_name": order.vendor.profile.business_name
        or order.vendor.profile.full_name,
        "order_type": order.order_type.value,
        "require_delivery": order.require_delivery,
        "total_price": str(order.total_price),
        "order_payment_status": order.order_payment_status.value,
        "order_status": order.order_status.value if order.order_status else None,
        "amount_due_vendor": str(order.amount_due_vendor),
        "payment_link": order.payment_link or "",
        "order_items": order_items,
        "created_at": order.created_at.isoformat(),
        "cancel_reason": getattr(order, "cancel_reason", None),
    }

    return DeliveryResponse(delivery=delivery_data, order=order_data)


async def get_user_profile(user_id: UUID, db: AsyncSession):
    result = await db.execute(select(Profile).where(Profile.user_id == user_id))

    return result.scalar_one_or_none()


# <<<<< --------- CACHE UTILITY FUNCTION ---------- >>>>>
CACHE_TTL = 3600  # 1 hour in seconds


def invalidate_delivery_cache(delivery_id: UUID) -> None:
    """
    Invalidate delivery cache when delivery is updated
    """
    redis_client.delete(f"delivery:{delivery_id}")
    # Also invalidate any cached list that might contain this delivery
    keys = redis_client.keys("all_deliveries:*")
    if keys:
        redis_client.delete(*keys)


def get_cached_order(order_id: UUID) -> dict:
    """Get order from cache"""
    cached_order = redis_client.get(f"order:{order_id}")
    return json.loads(cached_order) if cached_order else None


def set_cached_order(order_id: UUID, order_data: dict) -> None:
    """Set order in cache"""
    redis_client.setex(
        f"order:{order_id}",
        timedelta(seconds=CACHE_TTL),
        json.dumps(order_data, default=str),
    )


def invalidate_order_cache(order_id: UUID) -> None:
    """Invalidate order cache"""
    redis_client.delete(f"order:{order_id}")
    redis_client.delete("all_orders")


def filter_paid_pending_deliveries(
    deliveries: list[DeliveryResponse]
) -> list[DeliveryResponse]:
    """
    Filters deliveries where:
      - order_payment_status == 'paid'
      - delivery.delivery_status == 'pending'
      - order.require_delivery == 'delivery'
    """
    filtered = []
    for d in deliveries:
        order = getattr(d, "order", None)
        delivery = getattr(d, "delivery", None)
        if not order or not delivery:
            continue
        if (
            order.get("order_payment_status") == "paid"
            and delivery.get("delivery_status") == "pending"
            and order.get("require_delivery") == "delivery"
        ):
            filtered.append(d)
    return filtered


def filter_user_related_deliveries(
    deliveries: list[DeliveryResponse], user_id: UUID
) -> list[DeliveryResponse]:
    """
    Filters deliveries where the user is involved as:
      - order.user_id
      - order.vendor_id
      - delivery.dispatch_id
      - delivery.rider_id
    """
    filtered = []
    for d in deliveries:
        order = getattr(d, "order", None)
        delivery = getattr(d, "delivery", None)
        if not order:
            continue
        if (
            order.get("user_id") == user_id
            or order.get("vendor_id") == user_id
            or (
                delivery
                and (
                    delivery.get("dispatch_id") == user_id
                    or delivery.get("rider_id") == user_id
                )
            )
        ):
            filtered.append(d)
    return filtered


async def get_paid_pending_deliveries(db: AsyncSession) -> list[DeliveryResponse]:
    """
    Returns deliveries where:
      - order_payment_status == 'paid'
      - delivery.delivery_status == 'pending'
      - order.require_delivery == 'delivery'
    """

    cache_key = "paid_pending_deliveries"

    # Try cache first with error handling
    cached_deliveries = redis_client.get(cache_key)
    if cached_deliveries:
        return [DeliveryResponse(**d) for d in json.loads(cached_deliveries)]

    stmt = (
        select(Order)
        .where(
            and_(
                Order.order_payment_status == "paid",
                Order.require_delivery == "delivery",
                Order.delivery.has(delivery_status="pending"),
            )
        )
        .options(
            selectinload(Order.order_items).options(
                joinedload(OrderItem.item).options(selectinload(Item.images))
            ),
            joinedload(Order.delivery),
            joinedload(Order.vendor).joinedload(User.profile),
        )
        .order_by(Order.created_at.desc())
    )
    result = await db.execute(stmt)
    orders = result.unique().scalars().all()
    delivery_responses = [
        format_delivery_response(order, order.delivery) for order in orders
    ]

    redis_client.setex(
        cache_key,
        timedelta(seconds=settings.REDIS_EX),
        json.dumps([d.model_dump() for d in delivery_responses], default=str),
    )
    return delivery_responses


async def get_user_related_orders(
    db: AsyncSession,
    user_id: UUID,
) -> list[DeliveryResponse]:
    """
    Returns deliveries where the user is involved as:
      - order.owner_id
      - order.vendor_id
      - delivery.dispatch_id
      - delivery.rider_id
    """
    cache_key = f"user_related_orders:{user_id}"
    cached_orders = redis_client.get(cache_key)
    if cached_orders:
        return [DeliveryResponse(**d) for d in json.loads(cached_orders)]

    stmt = (
        select(Order)
        .outerjoin(
            Delivery
        )  # Using outerjoin to ensure we get orders even without deliveries
        .options(
            selectinload(Order.order_items).options(
                joinedload(OrderItem.item).options(selectinload(Item.images))
            ),
            joinedload(Order.delivery),
            joinedload(Order.vendor).joinedload(User.profile),
        )
        .where(
            or_(
                Order.owner_id == user_id,
                Order.vendor_id == user_id,
                and_(Delivery.id != None, Delivery.dispatch_id == user_id),
                and_(Delivery.id != None, Delivery.rider_id == user_id),
            )
        )
        .where(
            Order.order_type.in_([OrderType.FOOD, OrderType.PACKAGE, OrderType.LAUNDRY])
        )
        .order_by(Order.updated_at.desc())
    )
    result = await db.execute(stmt)
    orders = result.unique().scalars().all()

    delivery_responses = [
        format_delivery_response(order, order.delivery) for order in orders
    ]

    redis_client.setex(
        cache_key,
        timedelta(seconds=settings.REDIS_EX),
        json.dumps([d.model_dump() for d in delivery_responses], default=str),
    )

    return delivery_responses


async def cancel_order(
    db: AsyncSession,
    order_id: UUID,
    current_user: User,
    reason: str = None,
) -> DeliveryStatusUpdateSchema:
    """
    Cancel an order (with or without delivery). Sets order_status to CANCELLED, logs an audit, and updates caches.
    Args:
        db: Database session
        order_id: UUID of the order to cancel
        current_user: User performing the cancellation
        reason: Optional reason for cancellation
    Returns:
        DeliveryStatusUpdateSchema with the new order status
    """

    # Fetch the order
    order_result = await db.execute(
        select(Order).where(Order.id == order_id).options(selectinload(Order.delivery))
    )

    order = order_result.scalar_one_or_none()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    # Only allow owner or vendor to cancel
    if current_user.id not in [order.owner_id, order.vendor_id]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to cancel this order",
        )

    # If already cancelled, do nothing
    if order.order_status == OrderStatus.CANCELLED:
        return DeliveryStatusUpdateSchema(delivery_status=OrderStatus.CANCELLED)

    # Check if order was paid - only process refunds if payment was successful
    if order.order_payment_status != PaymentStatus.PAID:
        # Just update order status if not paid
        update_values = {"order_status": OrderStatus.CANCELLED}
        if reason:
            update_values["cancel_reason"] = reason
        await db.execute(
            update(Order).where(Order.id == order_id).values(**update_values)
        )

        # Cancel delivery if exists
        if order.delivery:
            await db.execute(
                update(Delivery)
                .where(Delivery.id == order.delivery.id)
                .values(delivery_status=DeliveryStatus.CANCELLED)
            )

        await db.commit()

        # Clear caches
        redis_client.delete(order_id)
        redis_client.delete(f"user_orders:{order.owner_id}")
        redis_client.delete(f"user_orders:{order.vendor_id}")
        redis_client.delete("orders")

        return DeliveryStatusUpdateSchema(delivery_status=OrderStatus.CANCELLED)

    # Process refunds for paid orders
    try:
        # Get wallets
        buyer_result = await db.execute(
            select(Wallet).where(Wallet.id == order.owner_id)
        )
        buyer_wallet = buyer_result.scalar_one_or_none()

        vendor_result = await db.execute(
            select(Wallet).where(Wallet.id == order.vendor_id)
        )
        vendor_wallet = vendor_result.scalar_one_or_none()

        if not buyer_wallet or not vendor_wallet:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Wallet not found for buyer or vendor",
            )

        # Calculate refund amounts
        total_refund = 0
        vendor_escrow_deduction = 0
        dispatch_escrow_deduction = 0

        # For package orders - refund delivery fee
        if order.order_type == OrderType.PACKAGE:
            if order.delivery and order.delivery.delivery_fee:
                total_refund = order.delivery.delivery_fee
                # Remove from dispatch escrow if assigned
                if order.dispatch_id:
                    dispatch_escrow_deduction = order.delivery.delivery_fee
        else:
            # For food/laundry orders - refund full amount
            total_refund = order.total_price
            if (
                order.require_delivery == RequireDeliverySchema.DELIVERY
                and order.delivery
            ):
                total_refund += order.delivery.delivery_fee
                if order.dispatch_id:
                    dispatch_escrow_deduction = order.delivery.delivery_fee

            vendor_escrow_deduction = order.total_price

        # Update buyer wallet - move money back to balance from escrow
        refund_amount = min(buyer_wallet.escrow_balance, total_refund)
        new_buyer_balance = buyer_wallet.balance + refund_amount
        new_buyer_escrow = max(buyer_wallet.escrow_balance - refund_amount, 0)

        await db.execute(
            update(Wallet)
            .where(Wallet.id == order.owner_id)
            .values(balance=new_buyer_balance, escrow_balance=new_buyer_escrow)
        )

        # Update vendor escrow if applicable
        if vendor_escrow_deduction > 0:
            new_vendor_escrow = max(
                vendor_wallet.escrow_balance - vendor_escrow_deduction, 0
            )
            await db.execute(
                update(Wallet)
                .where(Wallet.id == order.vendor_id)
                .values(escrow_balance=new_vendor_escrow)
            )

        # Update dispatch escrow if applicable
        if order.dispatch_id and dispatch_escrow_deduction > 0:
            dispatch_result = await db.execute(
                select(Wallet).where(Wallet.id == order.dispatch_id)
            )
            dispatch_wallet = dispatch_result.scalar_one_or_none()

            if dispatch_wallet:
                new_dispatch_escrow = max(
                    dispatch_wallet.escrow_balance - dispatch_escrow_deduction, 0
                )
                await db.execute(
                    update(Wallet)
                    .where(Wallet.id == order.dispatch_id)
                    .values(escrow_balance=new_dispatch_escrow)
                )

        # Update order status
        update_values = {"order_status": OrderStatus.CANCELLED}
        if reason:
            update_values["cancel_reason"] = reason
        await db.execute(
            update(Order).where(Order.id == order_id).values(**update_values)
        )

        # Update delivery status if exists
        if order.delivery:
            await db.execute(
                update(Delivery)
                .where(Delivery.id == order.delivery.id)
                .values(delivery_status=DeliveryStatus.CANCELLED)
            )

        # Create refund transaction record
        if refund_amount > 0:
            current_time = datetime.now()
            refund_tx = Transaction(
                wallet_id=buyer_wallet.id,
                amount=refund_amount,
                transaction_direction=TransactionDirection.CREDIT,
                transaction_type=TransactionType.REFUND,
                payment_status=PaymentStatus.PAID,
                payment_method=PaymentMethod.SYSTEM_REFUND,
                from_user="System Refund",
                to_user=current_user.profile.full_name
                or current_user.profile.business_name,
                created_at=current_time,
                updated_at=current_time,
            )
            db.add(refund_tx)

        # Commit all changes in one transaction
        await db.commit()

        # Clear caches - make sure these are async if redis_client expects async

        # If using sync redis client
        redis_client.delete(f"user_orders:{order.owner_id}")
        redis_client.delete(f"user_orders:{order.vendor_id}")
        redis_client.delete("orders")
        redis_client.delete("paid_pending_deliveries")

        return DeliveryStatusUpdateSchema(delivery_status=OrderStatus.CANCELLED)

    except Exception as e:
        # Rollback on error
        await db.rollback()
        logger_config.error(f"Error cancelling order {order_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel order",
        )


# async def cancel_order(
#     db: AsyncSession,
#     order_id: UUID,
#     current_user: User,
#     reason: str = None,
# ) -> DeliveryStatusUpdateSchema:
#     """
#     Cancel an order (with or without delivery). Sets order_status to CANCELLED, logs an audit, and updates caches.
#     Args:
#         db: Database session
#         order_id: UUID of the order to cancel
#         current_user: User performing the cancellation
#         reason: Optional reason for cancellation
#     Returns:
#         DeliveryStatusUpdateSchema with the new order status
#     """

#     # Fetch the order
#     order_result = await db.execute(
#         select(Order).where(Order.id == order_id).options(selectinload(Order.delivery))
#     )

#     order = order_result.scalar_one_or_none()
#     if not order:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
#         )

#     # Get Users Wallet
#     buyer_result = await db.execute(select(Wallet).where(Wallet.id == order.owner_id))
#     buyer_wallet = buyer_result.scalar_one_or_none()

#     vendor_result = await db.execute(select(Wallet).where(Wallet.id == order.vendor_id))
#     vendor_wallet = vendor_result.scalar_one_or_none()

#     # Only allow owner or vendor to cancel
#     if current_user.id not in [order.owner_id, order.vendor_id]:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Not authorized to cancel this order",
#         )

#     # If already cancelled, do nothing
#     if order.order_status == OrderStatus.CANCELLED:
#         return DeliveryStatusUpdateSchema(delivery_status=OrderStatus.CANCELLED)

#     old_status = order.order_status
#     update_values = {"order_status": OrderStatus.CANCELLED}
#     if reason:
#         update_values["cancel_reason"] = reason
#     await db.execute(update(Order).where(Order.id == order_id).values(**update_values))

#     # Refund buyer
#     refund_amount = min(buyer_wallet.escrow_balance, order.total_price)
#     new_buyer_balance = buyer_wallet.balance + refund_amount
#     new_buyer_escrow = max(buyer_wallet.escrow_balance - refund_amount, 0)
#     await db.execute(
#         update(Wallet)
#         .where(Wallet.id == order.owner_id)
#         .values({"balance": new_buyer_balance, "escrow_balance": new_buyer_escrow})
#     )
#     # Remove vendor escrow
#     new_vendor_escrow = max(vendor_wallet.escrow_balance - order.amount_due_vendor, 0)
#     await db.execute(
#         update(Wallet)
#         .where(Wallet.id == order.vendor_id)
#         .values({"escrow_balance": new_vendor_escrow})
#     )
#     # If delivery, remove dispatch escrow
#     if order.delivery:
#         dispatch_result = await db.execute(
#             select(Wallet).where(Wallet.id == order.dispatch_id)
#         )
#         dispatch_wallet = dispatch_result.scalar_one_or_none()
#         new_dispatch_escrow = max(
#             dispatch_wallet.escrow_balance - order.amount_due_dispatch, 0
#         )
#         await db.execute(
#             update(Wallet)
#             .where(Wallet.id == order.dispatch_id)
#             .values({"escrow_balance": new_dispatch_escrow})
#         )
#         await db.execute(
#             update(Delivery)
#             .where(Delivery.id == order.delivery.id)
#             .values(delivery_status=DeliveryStatus.CANCELLED)
#         )
#     await db.commit()
#     # Create refund transaction for buyer
#     refund_tx = Transaction(
#         wallet_id=buyer_wallet.id,
#         amount=refund_amount,
#         transaction_direction=TransactionDirection.CREDIT,
#         transaction_type=TransactionType.REFUND,
#         payment_status=PaymentStatus.PAID,
#         payment_by="system_refund",
#         created_at=datetime.now(),
#         updated_at=datetime.now(),
#     )
#     db.add(refund_tx)
#     await db.flush()

#     # If order has a delivery, optionally cancel delivery too
#     if order.delivery:
#         dispatch_result = await db.execute(
#             select(Wallet).where(Wallet.id == order.dispatch_id)
#         )
#         dispatch_wallet = dispatch_result.scalar_one_or_none()
#         await db.execute(
#             update(Delivery)
#             .where(Delivery.id == order.delivery.id)
#             .values(delivery_status=DeliveryStatus.CANCELLED)
#         )

#         await db.execute(
#             update(Wallet)
#             .where(Wallet.id == order.dispatch_id)
#             .values(
#                 {
#                     "escrow_balance": dispatch_wallet.escrow_balance
#                     - order.amount_due_vendor
#                 }
#             )
#         )
#         await db.commit()
#     # Invalidate caches
#     invalidate_order_cache(order_id)
#     redis_client.delete(f"user_orders:{order.owner_id}")
#     redis_client.delete(f"user_orders:{order.vendor_id}")
#     redis_client.delete("orders")
#     return DeliveryStatusUpdateSchema(delivery_status=OrderStatus.CANCELLED)


async def reaccept_order(
    db: AsyncSession,
    order_id: UUID,
    current_user: User,
) -> DeliveryStatusUpdateSchema:
    """
    Re-accept (re-list) a previously cancelled order. Sets order_status to PENDING and logs an audit.
    Args:
        db: Database session
        order_id: UUID of the order to re-accept
        current_user: User performing the action
    Returns:
        DeliveryStatusUpdateSchema with the new order status
    """
    order_result = await db.execute(
        select(Order).where(Order.id == order_id).options(selectinload(Order.delivery))
    )
    order = order_result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.order_status != OrderStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Order is not cancelled")
    old_status = order.order_status
    await db.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(order_status=OrderStatus.PENDING)
    )
    # Move funds from wallet back to escrow for buyer (reverse refund)

    buyer_result = await db.execute(select(Wallet).where(Wallet.id == order.owner_id))
    buyer_wallet = buyer_result.scalar_one_or_none()
    vendor_result = await db.execute(select(Wallet).where(Wallet.id == order.vendor_id))
    vendor_wallet = vendor_result.scalar_one_or_none()
    # Calculate amount to move back to escrow
    reescrow_amount = min(buyer_wallet.balance, order.total_price)
    new_buyer_balance = max(buyer_wallet.balance - reescrow_amount, 0)
    new_buyer_escrow = buyer_wallet.escrow_balance + reescrow_amount
    await db.execute(
        update(Wallet)
        .where(Wallet.id == order.owner_id)
        .values({"balance": new_buyer_balance, "escrow_balance": new_buyer_escrow})
    )
    # Move vendor escrow back if needed
    new_vendor_escrow = vendor_wallet.escrow_balance + order.amount_due_vendor
    await db.execute(
        update(Wallet)
        .where(Wallet.id == order.vendor_id)
        .values({"escrow_balance": new_vendor_escrow})
    )
    # If delivery, move dispatch escrow back if needed
    if order.delivery:
        dispatch_result = await db.execute(
            select(Wallet).where(Wallet.id == order.dispatch_id)
        )
        dispatch_wallet = dispatch_result.scalar_one_or_none()
        new_dispatch_escrow = dispatch_wallet.escrow_balance + order.amount_due_dispatch
        await db.execute(
            update(Wallet)
            .where(Wallet.id == order.dispatch_id)
            .values({"escrow_balance": new_dispatch_escrow})
        )
        await db.execute(
            update(Delivery)
            .where(Delivery.id == order.delivery.id)
            .values(delivery_status=DeliveryStatus.PENDING)
        )
    await db.commit()
    # Create debit transaction for buyer
    debit_tx = Transaction(
        wallet_id=buyer_wallet.id,
        amount=reescrow_amount,
        transaction_direction=TransactionDirection.DEBIT,
        transaction_type=TransactionType.REFUND,
        payment_status=PaymentStatus.PAID,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(debit_tx)
    await db.flush()
    await db.commit()

    # Invalidate caches
    invalidate_order_cache(order_id)
    redis_client.delete(f"user_orders:{order.owner_id}")
    redis_client.delete(f"user_orders:{order.vendor_id}")
    redis_client.delete("orders")
    return DeliveryStatusUpdateSchema(delivery_status=OrderStatus.PENDING)
