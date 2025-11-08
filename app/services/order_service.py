import asyncio
from datetime import datetime
from signal import raise_signal
from stat import ST_MTIME
from typing import Optional
from unittest import result
import uuid
from sqlalchemy import func, or_, and_, select, update, insert

from fastapi import UploadFile
from app.ws_manager.ws_manager import manager

from sqlalchemy.orm import joinedload, selectinload
from app.models.models import (
    AuditLog,
    ChargeAndCommission,
    Delivery,
    Item,
    Order,
    OrderItem,
    Transaction,
    User,
    Wallet,
    ItemImage,
    Profile,
)
from app.services import ws_service
from app.queue.producer import producer
from app.services.audit_log_service import TransactionLogService
from app.utils.map import get_distance_between_addresses


import json
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.status_schema import (
    OrderStatus,
    PaymentMethod,
    TransactionDirection,
    TransactionLogAction,
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
    CancelOrderSchema,
    LocationData,
)
from app.schemas.item_schemas import ItemType


from app.schemas.status_schema import RequireDeliverySchema, DeliveryStatus
from app.schemas.user_schemas import UserType, WalletRespose
from app.utils.logger_config import setup_logger


from app.utils.utils import (
    get_dispatch_id,
    get_payment_link,
    send_push_notification,
    get_user_notification_token,
)
from app.config.config import redis_client, settings
from app.utils.s3_service import add_image

logger = setup_logger()
ALL_DELIVERY = "orders"


async def get_order_by_id(
    order_id: UUID,
    db: AsyncSession,
) -> DeliveryResponse:
    """Get delivery by order ID"""

    cache_key = f"order_by_id:{order_id}"

    cached_delivery = redis_client.get(cache_key)
    if cached_delivery:
        delivery = json.loads(cached_delivery)
        return DeliveryResponse(**delivery)

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

        oder_response = format_delivery_response(order=order, delivery=order.delivery)

        redis_client.setex(
            cache_key,
            CACHE_TTL,
            json.dumps(oder_response.model_dump(), default=str),
        )

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
        format_delivery_response(order=order, delivery=order.delivery)
        for order in orders
    ]

    # Cache the formatted responses with error handling

    redis_client.setex(
        cache_key,
        CACHE_TTL,
        json.dumps([d.model_dump() for d in delivery_responses], default=str),
    )

    return delivery_responses


async def get_all_delivery_orders(
    db: AsyncSession, skip: int = 0, limit: int = 20
) -> PaginatedDeliveryResponse:
    """
    Get all orders with their deliveries (if any) with caching and total count
    """
    cache_key = f"delivery_orders-{skip}-{limit}"

    # Try cache first with error handling
    cached_deliveries = redis_client.get(cache_key)
    if cached_deliveries:
        cached = json.loads(cached_deliveries)
        return cached

    # 1. Get total count (without skip/limit)
    total_stmt = (
        select(func.count())
        .select_from(Order)
        .where(Order.order_type == OrderType.PACKAGE)
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
        .where(
            Order.require_delivery == RequireDeliverySchema.DELIVERY,
            Order.order_type == OrderType.PACKAGE,
        )
        .order_by(Order.created_at.desc())
    )

    result = await db.execute(stmt)
    orders = result.unique().scalars().all()

    delivery_responses = [
        format_delivery_response(order=order, delivery=order.delivery)
        for order in orders
    ]

    response = {"data": [d.model_dump() for d in delivery_responses], "total": total}

    # Cache the formatted responses with error handling
    redis_client.setex(
        cache_key,
        CACHE_TTL,
        json.dumps(response, default=str),
    )

    return response


async def _validate_package_order_request(current_user: User):
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


async def _create_package_item_and_image(
    db: AsyncSession, data: PackageCreate, image: UploadFile, current_user: User
):
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
        .returning(Item.id, Item.user_id)
    )
    package_data = package_insert_result.fetchone()

    image_url = await add_image(image)
    item_image = ItemImage(item_id=package_data.id, url=image_url)
    db.add(item_image)

    return package_data


async def _create_order_and_order_item(db: AsyncSession, package_data):
    order_insert_result = await db.execute(
        insert(Order)
        .values(
            {
                "owner_id": package_data.user_id,
                "vendor_id": package_data.user_id,
                "order_type": OrderType.PACKAGE,
                "amount_due_vendor": 0,
                "order_status": OrderStatus.PENDING,
                "order_payment_status": PaymentStatus.PENDING,
                "require_delivery": RequireDeliverySchema.DELIVERY,
            }
        )
        .returning(Order.id, Order.tx_ref, Order.owner_id, Order.vendor_id)
    )
    order_data = order_insert_result.fetchone()

    package_item_payload = {
        "order_id": order_data.id,
        "item_id": package_data.id,
        "quantity": 1,
    }
    await db.execute(insert(OrderItem).values(package_item_payload))

    return order_data


async def _create_delivery_and_calculate_fees(
    db: AsyncSession, data: PackageCreate, order_data, current_user: User
):
    delivery_fee = await calculate_delivery_fee(data.distance, db)
    amount_due_dispatch = await calculate_amount_due_dispatch(db, delivery_fee)

    delivery_values = {
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
    delivery_insert_result = await db.execute(
        insert(Delivery)
        .values(delivery_values)
        .returning(Delivery.id, Delivery.delivery_fee, Delivery.vendor_id, Delivery.rider_id, Delivery.dispatch_id)
    )
    return delivery_insert_result.fetchone()


async def _update_order_with_payment_link(
    db: AsyncSession, order_data, delivery_data, current_user: User
):
    total_amount_due = delivery_data.delivery_fee
    payment_link = await get_payment_link(
        tx_ref=order_data.tx_ref, amount=total_amount_due, current_user=current_user
    )
    await db.execute(
        update(Order)
        .where(Order.id == order_data.id)
        .values(
            payment_link=payment_link,
            total_price=total_amount_due,
            grand_total=total_amount_due,
        )
    )


async def _invalidate_package_order_caches(
    order_data, delivery_data, current_user: User
):
    redis_client.delete(f"user_orders:{current_user.id}")
    redis_client.delete(f"user_orders:{order_data.owner_id}")
    redis_client.delete(f"user_orders:{order_data.vendor_id}")
    redis_client.delete(f"user_orders:{delivery_data.rider_id}")
    redis_client.delete(f"user_orders:{delivery_data.dispatch_id}")
    redis_client.delete("near_by_riders")
    redis_client.delete(f"{ALL_DELIVERY}")
    redis_client.delete("paid_pending_deliveries")
    redis_client.delete(f"user_related_orders:{current_user.id}")
    redis_client.delete("orders")
    

async def create_package_order(
    db: AsyncSession, data: PackageCreate, image: UploadFile, current_user: User
) -> DeliveryResponse:
    """
    Creates a package order by orchestrating validation, database operations, and post-creation actions.
    """
    await _validate_package_order_request(current_user)

    try:
        # Create all database records within a nested transaction
        async with db.begin_nested():
            package_data = await _create_package_item_and_image(
                db, data, image, current_user
            )
            order_data = await _create_order_and_order_item(db, package_data)
            delivery_data = await _create_delivery_and_calculate_fees(
                db, data, order_data, current_user
            )

        

            await _assign_rider_and_update_db(
                db=db,
                order_id=order_data.id,
                delivery_id=delivery_data.id,
                rider_id=data.rider_id,
            )
            await _update_order_with_payment_link(
                db, order_data, delivery_data, current_user
            )

        # Commit the outer transaction
        await db.commit()



        # Invalidate caches after successful commit
        await _invalidate_package_order_caches(order_data, delivery_data, current_user)

        # Fetch the final order and delivery to return the response
        order_stmt = (
            select(Order)
            .where(Order.id == order_data.id)
            .options(
                selectinload(Order.order_items).options(
                    joinedload(OrderItem.item).options(selectinload(Item.images))
                )
            )
        )
        order = (await db.execute(order_stmt)).scalar_one()

        delivery_stmt = select(Delivery).where(Delivery.id == delivery_data.id)
        delivery = (await db.execute(delivery_stmt)).scalar_one()

        # rider_token = await get_user_notification_token(
        #         db=db, user_id=delivery.rider_id
        #     )
        # if rider_token:
        #     await send_push_notification(
        #         tokens=[rider_token],
        #         title="New order",
        #         message="You have a new order.",
        #         navigate_to="/delivery/orders",
        #     )


        redis_client.delete("near_by_riders")
        # Broadcast the new order
        await ws_service.broadcast_new_order({"order_id": str(order.id)})

        return format_delivery_response(order=order, delivery=delivery)

    except HTTPException:
        # Re-raise HTTP exceptions with their original status and detail
        await db.rollback()
        raise
    except Exception as e:
        # Handle unexpected errors
        await db.rollback()
        logger.error(f"Failed to create package order: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create package order. Please try again.",
        )


async def order_food_or_request_laundy_service(
    current_user: User,
    db: AsyncSession,
    vendor_id: UUID,
    order_item: OrderAndDeliverySchema,
) -> DeliveryResponse:
    """
    Creates a meal or laundry order.
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
                detail="Item(s) must belong to the same vendor!",
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
    # amount_due_vendor = await calculate_amount_due_vendor(db, order_item.order_items)

    # Determine if vendor delivery is required
    requires_delivery = (
        order_item.require_delivery == RequireDeliverySchema.VENDOR_PICKUP_AND_DROPOFF
    )

    # Calculate delivery charge based on require_delivery
    vendor_pickup_dropoff_charge = Decimal("0.00")

    if requires_delivery:
        # Fetch only the vendor's pickup/dropoff charge
        charge_result = await db.execute(
            select(Profile.pickup_and_delivery_charge).where(
                Profile.user_id == vendor_id
            )
        )
        charge = charge_result.scalar_one_or_none()

        if order_item.is_one_way_delivery is not True:
            vendor_pickup_dropoff_charge = Decimal(Decimal(charge) * 2)
        else:
            vendor_pickup_dropoff_charge = Decimal(charge)

    try:
        # Calculate final amount
        final_amount = total_price + vendor_pickup_dropoff_charge

        amount_due_vendor = await calculate_amount_due_vendor(
            db, total_price, vendor_pickup_dropoff_charge
        )

        # Create the order
        order_insert_result = await db.execute(
            insert(Order)
            .values(
                {
                    "owner_id": current_user.id,
                    "vendor_id": vendor_id,
                    "order_type": item_type,
                    "require_delivery": order_item.require_delivery,
                    "is_one_way_delivery": order_item.is_one_way_delivery,
                    "total_price": total_price,
                    "grand_total": final_amount,
                    "order_payment_status": PaymentStatus.PENDING,
                    "order_status": OrderStatus.PENDING,
                    "amount_due_vendor": amount_due_vendor,
                    "vendor_pickup_dropoff_charge": vendor_pickup_dropoff_charge,
                    "additional_info": order_item.additional_info,
                    "pickup_location": order_item.destination
                    if requires_delivery
                    else None,
                }
            )
            .returning(Order.id, Order.tx_ref, Order.grand_total)
        )

        order_id, tx_ref, grand_total = order_insert_result.fetchone()

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

        # Generate payment link
        payment_link = await get_payment_link(tx_ref, grand_total, current_user)

        # Update order with payment link
        await db.execute(
            update(Order)
            .where(Order.id == order_id)
            .values({"payment_link": payment_link})
        )

        await db.commit()

        # Clear relevant caches
        cache_keys = [
            f"user_orders:{current_user.id}",
            f"vendor_orders:{vendor_id}",
            f"order_details:{order_id}",
            f"user_related_orders:{current_user.id}",
            "orders",
        ]
        redis_client.delete(*cache_keys)

        # Fetch complete order data
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

        # Broadcast new order notification
        await ws_service.broadcast_new_order({"order_id": order.id})

        # Send push notification to vendor
        token = await get_user_notification_token(db=db, user_id=vendor_id)

        if token:
            await send_push_notification(
                tokens=[token],
                title="New Order",
                message=f"You have a new order from {current_user.profile.full_name if current_user.profile.full_name else current_user.profile.business_name}. Pending Payment",
                navigate_to="/delivery/orders",
            )

        return format_delivery_response(order=order, delivery=None)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create order - {e}",
        )


async def _validate_profile_and_authorization(
    current_user: User, order_items: list[OrderItemCreate], vendor_id: UUID
):
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
    for item_order in order_items:
        if current_user.id == item_order.vendor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot order your own item(s)!",
            )
    for vendor_item in order_items:
        if vendor_item.vendor_id != vendor_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Item(s) must belong to the same vendor!",
            )


async def _validate_order_items(
    db: AsyncSession, order_items: list[OrderItemCreate], vendor_id: UUID
):
    
    item_ids = [
        UUID(item.item_id) if isinstance(item.item_id, str) else item.item_id
        for item in order_items
    ]
    items_result = await db.execute(
        select(Item).where(Item.id.in_(item_ids)).where(Item.user_id == vendor_id)
    )
    items_data = {item.id: item for item in items_result.scalars().all()}

    if len(items_data) != len(item_ids):
        found_items = set(items_data.keys())
        missing_items = set(item_ids) - found_items
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Items not found or don't belong to this vendor: {missing_items}",
        )

    item_types = {items_data[item_id].item_type for item_id in item_ids}
    if len(item_types) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All items in the order must be of the same type (either all food or all laundry items)",
        )

    return items_data, item_types.pop()


async def _calculate_order_costs(
    db: AsyncSession,
    order_item_details: list[OrderItemCreate],
    items_data: dict,
    vendor_id: UUID,
    require_delivery: bool,
    is_one_way: bool,
):
    total_price = sum(
        Decimal(
            items_data[
                UUID(item.item_id) if isinstance(item.item_id, str) else item.item_id
            ].price
        )
        * Decimal(item.quantity)
        for item in order_item_details
    )

    vendor_pickup_dropoff_charge = Decimal("0.00")
    if require_delivery:
        charge_result = await db.execute(
            select(Profile.pickup_and_delivery_charge).where(
                Profile.user_id == vendor_id
            )
        )
        charge = charge_result.scalar_one_or_none() or Decimal("0.00")
        vendor_pickup_dropoff_charge = (
            Decimal(charge) if is_one_way else Decimal(charge) * 2
        )

    final_amount = total_price + vendor_pickup_dropoff_charge
    amount_due_vendor = await calculate_amount_due_vendor(
        db, total_price, vendor_pickup_dropoff_charge
    )

    return (
        total_price,
        vendor_pickup_dropoff_charge,
        final_amount,
        amount_due_vendor,
    )


async def _create_order_in_database(
    db: AsyncSession,
    current_user: User,
    vendor_id: UUID,
    order_item: OrderAndDeliverySchema,
    item_type: str,
    total_price: Decimal,
    final_amount: Decimal,
    amount_due_vendor: Decimal,
    vendor_pickup_dropoff_charge: Decimal,
    requires_delivery: bool,
):
    order_values = {
        "owner_id": current_user.id,
        "vendor_id": vendor_id,
        "order_type": item_type,
        "require_delivery": order_item.require_delivery,
        "is_one_way_delivery": order_item.is_one_way_delivery,
        "total_price": total_price,
        "grand_total": final_amount,
        "order_payment_status": PaymentStatus.PENDING,
        "order_status": OrderStatus.PENDING,
        "amount_due_vendor": amount_due_vendor,
        "vendor_pickup_dropoff_charge": vendor_pickup_dropoff_charge,
        "additional_info": order_item.additional_info,
        "pickup_location": order_item.destination if requires_delivery else None,
    }
    order_insert_result = await db.execute(
        insert(Order)
        .values(order_values)
        .returning(Order.id, Order.tx_ref, Order.grand_total)
    )
    order_id, tx_ref, grand_total = order_insert_result.fetchone()

    order_items_payload = [
        {"order_id": order_id, "item_id": item.item_id, "quantity": item.quantity}
        for item in order_item.order_items
    ]
    await db.execute(insert(OrderItem).values(order_items_payload))

    payment_link = await get_payment_link(tx_ref, grand_total, current_user)
    await db.execute(
        update(Order)
        .where(Order.id == order_id)
        .values({"payment_link": payment_link, "order_status": OrderStatus.PENDING})
    )

    return order_id


async def _handle_post_order_creation(
    db: AsyncSession, order_id: UUID, vendor_id: UUID, current_user: User
):
    cache_keys = [
        f"user_orders:{current_user.id}",
        f"vendor_orders:{vendor_id}",
        f"order_details:{order_id}",
        f"user_related_orders:{current_user.id}",
        "orders",
    ]
    redis_client.delete(*cache_keys)

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

    await ws_service.broadcast_new_order({"order_id": order.id})

    # token = await get_user_notification_token(db=db, user_id=vendor_id)
    # if token:
    #     await send_push_notification(
    #         tokens=[token],
    #         title="New Order",
    #         message=f"You have a new order from {current_user.profile.full_name or current_user.profile.business_name}",
    #         navigate_to="/delivery/orders",
    #     )

    return format_delivery_response(order=order, delivery=None)


async def create_food_or_laundry_order(
    current_user: User,
    db: AsyncSession,
    vendor_id: UUID,
    order_item: OrderAndDeliverySchema,
) -> DeliveryResponse:
    """
    Creates a meal or laundry order by orchestrating validation, calculation,
    database operations, and post-creation actions.
    """
    try:
        await _validate_profile_and_authorization(
            current_user, order_item.order_items, vendor_id
        )

        items_data, item_type = await _validate_order_items(
            db, order_item.order_items, vendor_id
        )

        requires_delivery = (
            order_item.require_delivery
            == RequireDeliverySchema.VENDOR_PICKUP_AND_DROPOFF
        )

        (
            total_price,
            vendor_pickup_dropoff_charge,
            final_amount,
            amount_due_vendor,
        ) = await _calculate_order_costs(
            db,
            order_item.order_items,
            items_data,
            vendor_id,
            requires_delivery,
            order_item.is_one_way_delivery,
        )

        async with db.begin_nested():
            order_id = await _create_order_in_database(
                db,
                current_user,
                vendor_id,
                order_item,
                item_type,
                total_price,
                final_amount,
                amount_due_vendor,
                vendor_pickup_dropoff_charge,
                requires_delivery,
            )

        await db.commit()

        return await _handle_post_order_creation(db, order_id, vendor_id, current_user)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create order: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create order - {e}",
        )


async def order_food_or_request_laundy_service_old(
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
                    "grand_total": final_amount,
                    "order_payment_status": PaymentStatus.PENDING,
                    "order_status": OrderStatus.PENDING,
                    "amount_due_vendor": amount_due_vendor,
                    "additional_info": order_item.additional_info,
                }
            )
            .returning(Order.id, Order.tx_ref, Order.grand_total)
        )

        order_id, tx_ref, grand_total = order_insert_result.fetchone()

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
        payment_link = await get_payment_link(tx_ref, grand_total, current_user)
        order_status = OrderStatus.PENDING

        # Update order with payment link
        await db.execute(
            update(Order)
            .where(Order.id == order_id)
            .values({"payment_link": payment_link, "order_status": order_status})
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
            return format_delivery_response(order=order, delivery=delivery)
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
            return format_delivery_response(order=order, delivery=None)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create order - {e}",
        )


async def cancel_order(
    db: AsyncSession, order_id: UUID, current_user: User, reason: CancelOrderSchema
) -> DeliveryStatusUpdateSchema:
    """
    Cancel an order and process associated refunds and notifications.

    Args:
        db: Database session
        order_id: UUID of the order to cancel
        current_user: User initiating the cancellation
        reason: Schema containing cancellation reason

    Returns:
        DeliveryStatusUpdateSchema with updated order status

    Raises:
        HTTPException: For validation errors, unauthorized access, or processing failures
    """

    try:
        # Fetch order with related data and lock
        order_stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.delivery),
                selectinload(Order.owner).selectinload(User.wallet),
                selectinload(Order.vendor).selectinload(User.wallet),
                joinedload(Order.owner).joinedload(User.profile),
                joinedload(Order.vendor).joinedload(User.profile),
            )
            .with_for_update()
        )
        order_result = await db.execute(order_stmt)
        order = order_result.unique().scalar_one_or_none()

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
            )

        # Validate authorization
        if current_user.id not in [order.owner_id, order.vendor_id]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to cancel this order",
            )

        # Validate order status
        if order.order_status in [OrderStatus.RECEIVED, OrderStatus.CANCELLED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Order is already {order.order_status} and cannot be cancelled",
            )

        # Validate payment status for refunds
        if order.order_payment_status != PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only paid orders can be cancelled with refund",
            )

        # Process cancellation
        order.order_status = OrderStatus.CANCELLED
        order.cancel_reason = reason.reason

        # Process refunds based on cancelling user
        if current_user.id == order.owner_id:
            # Return funds to owner
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.owner_id),
                    "balance_change": str(order.grand_total),
                    "escrow_change": str(-order.grand_total),
                },
            )

            # Record refund transaction
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(current_user.id),
                    "tx_ref": str(uuid.uuid4()),
                    "amount": str(order.grand_total),
                    "transaction_type": TransactionType.ORDER_CANCELLATION,
                    "transaction_direction": TransactionDirection.CREDIT,
                    "payment_status": PaymentStatus.PAID,
                    "payment_method": PaymentMethod.FUND_REVERSAL,
                    "from_user": "System Reversal",
                    "details": {"reason": reason.reason},
                },
            )

        elif current_user.id == order.vendor_id:
            # Update vendor escrow
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.vendor_id),
                    "balance_change": "0",
                    "escrow_change": str(-order.grand_total),
                },
            )

            # Record vendor transaction
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(current_user.id),
                    "tx_ref": str(uuid.uuid4()),
                    "amount": str(-order.grand_total),
                    "transaction_type": TransactionType.ORDER_CANCELLATION,
                    "transaction_direction": TransactionDirection.DEBIT,
                    "payment_status": PaymentStatus.PAID,
                    "payment_method": PaymentMethod.FUND_REVERSAL,
                    "from_user": "System Reversal",
                    "details": {"reason": reason.reason},
                },
            )

        # Log the transaction
        await TransactionLogService.create_log(
            db=db,
            vendor_id=current_user.id,
            order_id=order.id,
            amount=order.grand_total,
            action=TransactionLogAction.REFUNDED,
            status=order.order_payment_status,
            details={
                "order_type": order.order_type,
                "order_number": order.order_number,
                "canceled_by": current_user.profile.full_name
                or current_user.profile.business_name,
                "phone_number": current_user.profile.phone_number,
                "reason": reason.reason,
            },
        )

        # Invalidate caches
        cache_keys = [
            f"user_orders:{order.owner_id}",
            f"user_orders:{order.vendor_id}",
            f"order_details:{order.id}",
            f"user_related_orders:{current_user.id}",
            ALL_DELIVERY,
            "orders",
        ]
        redis_client.delete(*cache_keys)

        # Send notifications
        try:
            # Notify other party
            notify_user_id = (
                order.vendor_id if current_user.id == order.owner_id else order.owner_id
            )
            user_token = await get_user_notification_token(
                db=db, user_id=notify_user_id
            )
            if user_token:
                await send_push_notification(
                    tokens=[user_token],
                    title="Order Cancelled",
                    message=f"Order #{order.order_number} has been cancelled by {'customer' if current_user.id == order.owner_id else 'vendor'}",
                    navigate_to="/delivery/orders",
                )
        except Exception as e:
            logger.warning(f"Failed to send cancellation notification: {e}")

        # Broadcast status update
        await ws_service.broadcast_order_status_update(
            order_id=str(order.id), new_status=OrderStatus.CANCELLED.value
        )

        # Cancel associated delivery if exists
        if order.delivery:
            order.delivery.delivery_status = DeliveryStatus.CANCELLED
            await ws_service.broadcast_delivery_status_update(
                delivery_id=str(order.delivery.id),
                new_status=DeliveryStatus.CANCELLED.value,
            )

        return DeliveryStatusUpdateSchema(
            order_status=OrderStatus.CANCELLED,
            delivery_status=DeliveryStatus.CANCELLED if order.delivery else None,
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to cancel order {order_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process order cancellation: {str(e)}",
        )


async def cancel_delivery(
    db: AsyncSession, current_user: User, reason: CancelOrderSchema
) -> DeliveryStatusUpdateSchema:
    """
    Cancel a delivery order with proper validation and transaction handling.
    This function handles both rider/dispatch cancellations (which re-list the delivery)
    and sender cancellations (which process refunds).

    Args:
        db: Database session
        order_id: UUID of the order to cancel
        current_user: Currently authenticated user
        reason: Schema containing cancellation reason

    Returns:
        DeliveryStatusUpdateSchema with updated order and delivery status

    Raises:
        HTTPException: For validation errors, unauthorized access, or processing failures
    """

    try:
        order = await _order_to_cancel(db=db, order_id=reason.order_id)
        await _cancel_delivery_validation(order, current_user)

        if current_user.user_type == UserType.RIDER:
            # Rider/Dispatch cancellation
            status_update = await _rider_cancel_delivery(
                order, db, current_user, reason.reason
            )
        else:
            # Sender cancellation: Process refund
            status_update = await _sender_cancel_delivery(
                order, db, current_user, reason.reason
            )

        # Log the transaction
        await TransactionLogService.create_log(
            db=db,
            vendor_id=current_user.id,
            order_id=order.id,
            amount=order.grand_total,
            action=TransactionLogAction.REFUNDED,
            status=order.order_payment_status,
            details={
                "order_type": order.order_type,
                "order_number": order.order_number,
                "canceled_by": current_user.profile.full_name
                or current_user.profile.business_name,
                "phone_number": current_user.profile.phone_number,
                "reason": reason.reason,
            },
        )
        redis_client.delete(f"order_by_id:{order.id}")
        await db.commit()
        return status_update

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to cancel delivery {order.id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel delivery: {str(e)}",
        )


async def _cancel_delivery_validation(order: Order, current_user: User):
    """
    Validate that the delivery can be cancelled by checking authorization and status.

    Args:
        order: The order with delivery to validate
        current_user: The user attempting to cancel

    Raises:
        HTTPException: If validation fails
    """
    # Validate delivery exists
    if not order.delivery:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This order has no delivery to cancel.",
        )

    # Check authorization
    allowed_user_ids = {
        order.delivery.rider_id,
        order.delivery.sender_id,
    }

    if current_user.id not in allowed_user_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to cancel this order.",
        )

    # Check delivery status
    if order.delivery.delivery_status in [
        DeliveryStatus.RECEIVED,
        DeliveryStatus.CANCELLED,
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Delivery is already {order.delivery.delivery_status} and cannot be cancelled.",
        )

    # Validate status based on user type
    if (
        current_user.user_type == UserType.RIDER
        and current_user.id == order.delivery.rider_id
    ):
        if order.delivery.delivery_status != DeliveryStatus.ACCEPTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Riders can only cancel accepted deliveries.",
            )

    elif order.delivery.sender_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the sender can cancel this delivery.",
        )



async def _order_to_cancel(db: AsyncSession, order_id: UUID) -> Order:
    """
    Fetch an order with all related data needed for cancellation.
    Uses SELECT FOR UPDATE to prevent concurrent modifications.

    Args:
        db: Database session
        order_id: UUID of the order to fetch

    Returns:
        Order with loaded relationships: delivery → sender, rider

    Raises:
        HTTPException: If order not found or database error occurs
    """
    try:
        order_stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.delivery).options(
                    selectinload(Delivery.sender),   
                    selectinload(Delivery.rider),  
                )
            )
            .with_for_update()
        )

        order_result = await db.execute(order_stmt)
        order = order_result.unique().scalar_one_or_none()

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found"
            )

        if not order.delivery:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order has no associated delivery"
            )

        if not order.delivery.sender or not order.delivery.rider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Delivery is missing sender or rider"
            )

        return order

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching order {order_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve order"
        )

async def _rider_cancel_delivery(
    order: Order, db: AsyncSession, current_user: User, reason: str
) -> DeliveryStatusUpdateSchema:
    """
    Handle cancellation of a delivery by a rider or dispatch company.
    This re-lists the delivery for other riders to accept.
    
    Note: If rider cancels BEFORE pickup, no escrow reversal is needed.
          If rider cancels AFTER pickup, we need to reverse the escrow.
    """
    try:
        old_dispatch_id = order.delivery.dispatch_id
        old_rider_id = order.delivery.rider_id
        was_picked_up = order.delivery.delivery_status == DeliveryStatus.PICKED_UP

        # Validation
        if current_user.user_type == UserType.RIDER:
            if order.delivery.rider_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Riders can only cancel their own assigned deliveries.",
                )
            if order.delivery.delivery_status not in [DeliveryStatus.ACCEPTED, DeliveryStatus.PICKED_UP]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Riders can only cancel accepted or picked-up deliveries.",
                )

        # Re-list the delivery
        async with db.begin_nested():
            order.order_status = OrderStatus.PENDING
            order.delivery.delivery_status = DeliveryStatus.PENDING
            order.delivery.rider_id = None
            order.delivery.dispatch_id = None
            order.delivery.rider_phone_number = None
            order.cancel_reason = reason

            # Track rider cancellations
            if current_user.user_type == UserType.RIDER:
                current_user.order_cancel_count = (
                    current_user.order_cancel_count or 0
                ) + 1

            await db.flush()

            await db.execute(update(User).where(User.id==old_rider_id).values(User.has_delivery==False))

        if was_picked_up and old_dispatch_id and order.order_payment_status == PaymentStatus.PAID:
            logger.info(f"Reversing pickup escrow for cancelled order {order.id}")
            
            # Reverse the escrow allocation that happened at pickup
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(old_dispatch_id),
                    "balance_change": "0",
                    "escrow_change": str(-order.delivery.delivery_fee),
                    "idempotency_key": f"cancel_pickup_escrow:{order.id}",
                    "details": {
                        "order_id": str(order.id),
                        "operation": "cancel_pickup_escrow_reversal",
                        "order_number": order.order_number,
                        "reason": reason,
                    },
                },
            )

            # Record reversal transaction
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(old_dispatch_id),
                    "tx_ref": str(uuid.uuid4()),
                    "amount": str(order.delivery.delivery_fee),
                    "transaction_type": TransactionType.ORDER_CANCELLATION,
                    "transaction_direction": TransactionDirection.DEBIT,
                    "payment_status": PaymentStatus.PAID,
                    "payment_method": PaymentMethod.FUND_REVERSAL,
                    "from_user": "System Reversal - Rider Cancelled After Pickup",
                    "details": {"reason": reason, "cancelled_at_stage": "picked_up"},
                },
            )
            logger.info(f"Pickup escrow reversed for order {order.id}")
        else:
            logger.info(f"No escrow reversal needed for order {order.id} - was not picked up yet")

        # Rest of cancellation logic...
        await db.commit()
        
        # Invalidate caches
        cache_keys = [
            f"user_orders:{order.owner_id}",
            f"user_orders:{order.vendor_id}",
            f"order_details:{order.id}",
            f"delivery:{order.delivery.id}",
            f"user_related_orders:{current_user.id}",
            f"order_by_id:{order.id}",
            "paid_pending_deliveries",
            ALL_DELIVERY,
            "orders",
        ]
            
        if order.delivery.rider_id:
            cache_keys.append(f"user_related_orders:{order.delivery.rider_id}")
            cache_keys.append(f"user_orders:{order.delivery.rider_id}")
        if order.delivery.dispatch_id:
            cache_keys.append(f"user_related_orders:{order.delivery.dispatch_id}")
            cache_keys.append(f"user_orders:{order.delivery.dispatch_id}")
            
        redis_client.delete(*cache_keys)

        try:
            # Notify sender (confirmation)
            sender_token = await get_user_notification_token(
                db=db, user_id=order.owner_id
            )
            if sender_token:
                await send_push_notification(
                    tokens=[sender_token],
                    title="Delivery Cancelled",
                    message=(
                        f"We're sorry, but your rider cancelled order #{order.order_number}. Don't worry - the delivery fee has been credited to your wallet. You can select a new rider and pay to continue."
                          ),
                    navigate_to="/delivery/orders",
                )

            # Notify rider if exists and is different from sender
            if order.delivery.rider_id and was_picked_up:
                rider_token = await get_user_notification_token(
                    db=db, user_id=order.delivery.rider_id
                )
                if rider_token:
                    await send_push_notification(
                        tokens=[rider_token],
                        title="Delivery Cancelled",
                        message=(f"You have cancelled order #{order.order_number}."),
                        navigate_to="/delivery/orders",
                    )


        except Exception as e:
            logger.warning(
                f"Failed to send cancellation notifications for order {order.id}: {str(e)}"
            )


        
        return DeliveryStatusUpdateSchema(
            order_status=OrderStatus.PENDING, 
            delivery_status=DeliveryStatus.PENDING
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error in rider cancellation for order {order.id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel delivery: {str(e)}",
        )


async def _sender_cancel_delivery(
    order: Order, db: AsyncSession, current_user: User, reason: CancelOrderSchema
) -> DeliveryStatusUpdateSchema:
    """
    Process a sender-initiated delivery cancellation.
    
    Flow:
    - If NOT picked up yet: Full refund to sender's balance
    - If PICKED UP: No refund (sender will be charged when they confirm return)
    - Set order and delivery status to CANCELLED
    
    Args:
        order: Order instance with loaded delivery relationship
        db: Database session
        current_user: The sender cancelling the order
        reason: Cancellation reason
        
    Returns:
        DeliveryStatusUpdateSchema with cancelled statuses
        
    Raises:
        HTTPException: If validation fails or processing error occurs
    """
    try:
        # Validation
        if order.delivery.sender_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Only the sender can cancel this delivery"
            )

        if not order.delivery:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This order has no delivery to cancel.",
            )

        # Check if already cancelled
        if order.order_status == OrderStatus.CANCELLED:
            logger.info(f"Order {order.id} already cancelled")
            return DeliveryStatusUpdateSchema(
                order_status=OrderStatus.CANCELLED,
                delivery_status=DeliveryStatus.CANCELLED
            )

        # Validate payment status - only process refunds if paid
        if order.order_payment_status != PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel - order payment is not completed"
            )

        # Check if package was already picked up
        was_picked_up = order.delivery.delivery_status in [
            DeliveryStatus.PICKED_UP, 
            DeliveryStatus.DELIVERED
        ]
        
        # Idempotency key for this cancellation
        idempotency_key = f"sender_cancel:{order.id}:{order.delivery.id}"
        cache_key = f"idempotency:{idempotency_key}"
        
        # Check if already processed
        if redis_client.get(cache_key):
            logger.info(f"Sender cancellation for order {order.id} already processed")
            return DeliveryStatusUpdateSchema(
                order_status=order.order_status,
                delivery_status=order.delivery.delivery_status
            )

        # Set processing lock
        redis_client.setex(cache_key, 300, "processing")

        try:
            # ONLY refund if package was NOT picked up yet
            if not was_picked_up:
                logger.info(
                    f"Package not picked up yet. Refunding {order.delivery.delivery_fee} to sender's balance"
                )
                
                # Credit FULL delivery_fee to sender's wallet BALANCE
                await producer.publish_message(
                    service="wallet",
                    operation="update_wallet",
                    payload={
                        "wallet_id": str(order.delivery.sender_id),
                        "balance_change": str(order.delivery.delivery_fee),  
                        "escrow_change": str(-order.delivery.delivery_fee),
                        "idempotency_key": f"{idempotency_key}:sender_refund",
                        "details": {
                            "order_id": str(order.id),
                            "operation": "sender_cancel_refund_before_pickup",
                            "order_number": order.order_number,
                            "reason": reason,
                        },
                    },
                )

                # Record refund transaction
                await producer.publish_message(
                    service="wallet",
                    operation="create_transaction",
                    payload={
                        "wallet_id": str(order.delivery.sender_id),
                        "tx_ref": str(uuid.uuid4()),
                        "amount": str(order.delivery.delivery_fee),
                        "transaction_type": TransactionType.ORDER_CANCELLATION,
                        "transaction_direction": TransactionDirection.CREDIT,
                        "payment_status": PaymentStatus.PAID,
                        "payment_method": PaymentMethod.FUND_REVERSAL,
                        "from_user": "System Refund - Cancelled Before Pickup",
                        "to_user": current_user.profile.full_name or current_user.profile.business_name,
                        "idempotency_key": f"{idempotency_key}:sender_tx",
                        "details": {
                            "reason": reason,
                            "refund_to": "balance",
                            "message": "Full refund - package was not picked up yet."
                        },
                    },
                )
                
                refund_message = f"₦{order.delivery.delivery_fee} has been refunded to your wallet balance."
            else:
                logger.info(
                    f"Package was already picked up for order {order.id}. "
                    f"No refund. Sender will be charged when they confirm return."
                )
                refund_message = (
                    "The rider will return your package. You will be charged the delivery fee "
                    "when you confirm receipt of the returned item."
                )

            rider_id = order.delivery.rider_id
            dispatch_id = order.delivery.dispatch_id

            # Update order and delivery statuses
            order.order_status = OrderStatus.CANCELLED
            order.delivery.delivery_status = DeliveryStatus.CANCELLED
            order.order_payment_status = PaymentStatus.CANCELLED
            order.cancel_reason = reason

            # Clear rider/dispatch assignments
            order.delivery.rider_id = None
            order.delivery.dispatch_id = None
            order.delivery.rider_phone_number = None
          

            await db.execute(update(User).where(User.id==rider_id).values(User.has_delivery==False))
            
            await db.commit()

            # Mark as completed
            redis_client.setex(cache_key, 86400, "completed")

            logger.info(
                f"Sender cancellation completed for order {order.id}. "
                f"Was picked up: {was_picked_up}"
            )

            # Invalidate caches
            cache_keys = [
                f"user_orders:{order.owner_id}",
                f"user_orders:{order.vendor_id}",
                f"order_details:{order.id}",
                f"delivery:{order.delivery.id}",
                f"user_related_orders:{current_user.id}",
                f"order_by_id:{order.id}",
                "paid_pending_deliveries",
                ALL_DELIVERY,
                "orders",
            ]
            
            if order.delivery.rider_id:
                cache_keys.append(f"user_related_orders:{order.delivery.rider_id}")
                cache_keys.append(f"user_orders:{order.delivery.rider_id}")
            if order.delivery.dispatch_id:
                cache_keys.append(f"user_related_orders:{order.delivery.dispatch_id}")
                cache_keys.append(f"user_orders:{order.delivery.dispatch_id}")
                
            redis_client.delete(*cache_keys)

            # Send notifications
            try:
                # Notify sender (confirmation)
                sender_token = await get_user_notification_token(
                    db=db, user_id=order.owner_id
                )
                if sender_token:
                    await send_push_notification(
                        tokens=[sender_token],
                        title="Delivery Cancelled",
                        message=(
                            f"Your delivery for order #{order.order_number} has been cancelled. "
                            f"{refund_message}"
                        ),
                        navigate_to="/delivery/orders",
                    )

                # Notify rider if exists and is different from sender
                if order.delivery.rider_id and was_picked_up:
                    rider_token = await get_user_notification_token(
                        db=db, user_id=order.delivery.rider_id
                    )
                    if rider_token:
                        await send_push_notification(
                            tokens=[rider_token],
                            title="Delivery Cancelled",
                            message=(
                               f"⚠️ The sender has cancelled order #{order.order_number}. Please return the item to the pickup address. Don't worry - you'll still receive your full payment."
                            ),
                            navigate_to="/delivery/orders",
                        )


            except Exception as e:
                logger.warning(
                    f"Failed to send cancellation notifications for order {order.id}: {str(e)}"
                )

            # Broadcast status updates
            await ws_service.broadcast_order_status_update(
                order_id=str(order.id), 
                new_status=OrderStatus.CANCELLED.value
            )
            await ws_service.broadcast_delivery_status_update(
                delivery_id=str(order.delivery.id),
                new_status=DeliveryStatus.CANCELLED.value,
            )

            return DeliveryStatusUpdateSchema(
                order_status=OrderStatus.CANCELLED, 
                delivery_status=DeliveryStatus.CANCELLED
            )

        except Exception as wallet_error:
            # Cleanup on wallet operation failure
            redis_client.delete(cache_key)
            raise wallet_error

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error in sender cancellation for order {order.id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel delivery: {str(e)}",
        )


async def _process_post_delivery_cancellation_rider(
    order: Order,
    db: AsyncSession,
    old_dispatch_id: UUID,
    current_user: User,
    reason: str,
) -> DeliveryStatusUpdateSchema:
    """
    Process a rider/dispatch-initiated delivery cancellation including escrow updates,
    cache invalidation, and notifications.

    Args:
        order: The order being cancelled
        db: Database session
        old_dispatch_id: ID of the previous dispatch company
        current_user: The user cancelling the order
        reason: Cancellation reason

    Returns:
        DeliveryStatusUpdateSchema with updated statuses
    """
    try:
        # 1. Process escrow updates if order was paid
        if old_dispatch_id and order.order_payment_status == PaymentStatus.PAID:
            # Update dispatch company wallet
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(old_dispatch_id),
                    "balance_change": "0",
                    "escrow_change": str(-order.delivery.amount_due_dispatch),
                },
            )

            # Record refund transaction
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(old_dispatch_id),
                    "tx_ref": str(uuid.uuid4()),
                    "amount": str(order.delivery.amount_due_dispatch),
                    "transaction_type": TransactionType.ORDER_CANCELLATION,
                    "transaction_direction": TransactionDirection.CREDIT,
                    "payment_status": PaymentStatus.PAID,
                    "payment_method": PaymentMethod.FUND_REVERSAL,
                    "from_user": "System Reversal",
                    "details": {"reason": reason},
                },
            )

        # 2. Update rider cancellation count
        if current_user.user_type == UserType.RIDER:
            current_user.order_cancel_count = (current_user.order_cancel_count or 0) + 1

        # 3. Reset delivery assignment
        order.order_status = OrderStatus.PENDING
        order.delivery.delivery_status = DeliveryStatus.PENDING
        order.delivery.rider_id = None
        order.delivery.dispatch_id = None
        order.delivery.rider_phone_number = None
        order.cancel_reason = reason
        await db.commit()

        # 4. Invalidate caches
        cache_keys = [
            f"user_orders:{order.owner_id}",
            f"order_details:{order.id}",
            f"delivery:{order.delivery.id}",
            f"user_related_orders:{current_user.id}",
            f"order_by_id:{order.id}" ,
            "paid_pending_deliveries",
            ALL_DELIVERY,
            "orders",
        ]
        redis_client.delete(*cache_keys)

        # 5. Send notifications
        try:
            # Notify customer
            customer_token = await get_user_notification_token(
                db=db, user_id=order.owner_id
            )
            if customer_token:
                await send_push_notification(
                    tokens=[customer_token],
                    title="Delivery Canceled",
                    message=(
                        f"The delivery for your order #{order.order_number} was cancelled "
                        f"by the {current_user.user_type.lower()}. It is now available for other riders."
                    ),
                    navigate_to="/delivery/orders",
                )

            # Notify vendor if exists
            if order.vendor_id:
                vendor_token = await get_user_notification_token(
                    db=db, user_id=order.vendor_id
                )
                if vendor_token:
                    await send_push_notification(
                        tokens=[vendor_token],
                        title="Delivery Canceled",
                        message=f"Delivery for order #{order.order_number} was cancelled by the rider.",
                        navigate_to="/delivery/orders",
                    )
        except Exception as e:
            logger.warning(f"Failed to send cancellation notifications: {e}")

        # 6. Broadcast WebSocket updates
        await ws_service.broadcast_order_status_update(
            order_id=str(order.id), new_status=OrderStatus.PENDING.value
        )
        await ws_service.broadcast_delivery_status_update(
            delivery_id=str(order.delivery.id), new_status=DeliveryStatus.PENDING.value
        )

        return DeliveryStatusUpdateSchema(
            order_status=OrderStatus.PENDING, delivery_status=DeliveryStatus.PENDING
        )

    except Exception as e:
        logger.error(
            f"Error in rider cancellation processing for order {order.id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process delivery cancellation: {str(e)}",
        )


async def _process_post_delivery_cancellation_sender(
    order: Order, db: AsyncSession, current_user: User, reason: str
) -> DeliveryStatusUpdateSchema:
    """
    Process a sender-initiated delivery cancellation including refunds and notifications.

    Args:
        order: The order being cancelled
        db: Database session
        current_user: The user cancelling the order
        reason: Cancellation reason

    Returns:
        DeliveryStatusUpdateSchema with updated statuses
    """
    try:
        # 1. Process refund
        if order.order_payment_status == PaymentStatus.PAID:
            # Move escrow funds to sender wallet
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.delivery.sender_id),
                    "balance_change": str(order.delivery.delivery_fee),
                    "escrow_change": str(-order.delivery.delivery_fee),
                },
            )

            # Record refund transaction
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(order.delivery.sender_id),
                    "tx_ref": str(uuid.uuid4()),
                    "amount": str(order.delivery.delivery_fee),
                    "transaction_type": TransactionType.ORDER_CANCELLATION,
                    "transaction_direction": TransactionDirection.CREDIT,
                    "payment_status": PaymentStatus.PAID,
                    "payment_method": PaymentMethod.FUND_REVERSAL,
                    "from_user": "System Reversal",
                    "details": {"reason": reason},
                },
            )

        # 2. Update order statuses
        order.order_status = OrderStatus.CANCELLED
        order.delivery.delivery_status = DeliveryStatus.CANCELLED
        order.cancel_reason = reason
        await db.commit()

        # 3. Invalidate caches
        cache_keys = [
            f"user_orders:{order.owner_id}",
            f"user_orders:{order.vendor_id}",
            f"order_details:{order.id}",
            f"delivery:{order.delivery.id}",
            f"user_related_orders:{current_user.id}",
            "paid_pending_deliveries",
            ALL_DELIVERY,
            "orders",
        ]
        redis_client.delete(*cache_keys)

        # 4. Send notifications
        try:
            vendor_token = await get_user_notification_token(
                db=db, user_id=order.vendor_id
            )
            if vendor_token:
                await send_push_notification(
                    tokens=[vendor_token],
                    title="Delivery Cancelled",
                    message=f"Delivery for order #{order.order_number} has been cancelled by the sender.",
                    navigate_to="/delivery/orders",
                )
        except Exception as e:
            logger.warning(f"Failed to send cancellation notification: {e}")

        # 5. Broadcast status updates
        await ws_service.broadcast_order_status_update(
            order_id=str(order.id), new_status=OrderStatus.CANCELLED.value
        )
        await ws_service.broadcast_delivery_status_update(
            delivery_id=str(order.delivery.id),
            new_status=DeliveryStatus.CANCELLED.value,
        )

        return DeliveryStatusUpdateSchema(
            order_status=OrderStatus.CANCELLED, delivery_status=DeliveryStatus.CANCELLED
        )

    except Exception as e:
        logger.error(
            f"Error in sender cancellation processing for order {order.id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process delivery cancellation: {str(e)}",
        )


async def cancel_order_or_delivery(
    db: AsyncSession, order_id: UUID, current_user: User, reason: CancelOrderSchema
) -> DeliveryStatusUpdateSchema:
    """
    Cancel an order and its associated delivery. This action is irreversible and will
    process refunds for paid orders.

    Args:
        db: The database session.
        order_id: The ID of the order to cancel.
        current_user: The user initiating the cancellation.

    Returns:
        A schema indicating the new status of the order and delivery.

    Raises:
        HTTPException: If the order is not found, the user is not authorized,
                       or the order is already in a final state.
    """
    async with db.begin():
        # 1. Fetch the order with all related entities and lock it for update
        order_stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.delivery),
                selectinload(Order.owner).selectinload(User.wallet),
                selectinload(Order.vendor).selectinload(User.wallet),
            )
            .with_for_update()
        )
        order_result = await db.execute(order_stmt)
        order = order_result.unique().scalar_one_or_none()

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
            )

        # 2. Check authorization
        allowed_user_ids = {order.owner_id, order.vendor_id}
        if order.delivery:
            allowed_user_ids.add(order.delivery.rider_id)
            allowed_user_ids.add(order.delivery.dispatch_id)

        if current_user.id not in allowed_user_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to cancel this order.",
            )

        # 3. Check if order is already in a final state
        if order.order_status in [OrderStatus.RECEIVED, OrderStatus.CANCELLED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Order is already {order.order_status} and cannot be cancelled.",
            )

        # --- Rider Cancellation (Re-list) ---
        if current_user.user_type == UserType.RIDER:
            if not order.delivery:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This order has no delivery to cancel.",
                )

            # Authorization check: Rider can cancel their own delivery.
            # Dispatch can cancel a delivery assigned to their company.
            is_rider_of_delivery = (
                current_user.user_type == UserType.RIDER
                and order.delivery.rider_id == current_user.id
            )

            if not is_rider_of_delivery:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not authorized to cancel this specific delivery.",
                )

            if order.order_status != OrderStatus.ACCEPTED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot cancel a delivery that has not been accepted.",
                )

            # Re-list logic starts here
            old_dispatch_id = order.delivery.dispatch_id  # Store before clearing
            order.order_status = OrderStatus.PENDING
            order.delivery.delivery_status = DeliveryStatus.PENDING
            order.delivery.rider_id = None
            order.delivery.dispatch_id = None
            order.delivery.rider_phone_number = None
            order.cancel_reason = reason.reason

            # Increment rider's cancellation count if a rider is cancelling
            if current_user.user_type == UserType.RIDER:
                current_user.order_cancel_count = (
                    current_user.order_cancel_count or 0
                ) + 1

            # Reverse escrow for the original dispatch company if order was paid
            if old_dispatch_id and order.order_payment_status == PaymentStatus.PAID:
                await producer.publish_message(
                    service="wallet",
                    operation="update_wallet",
                    payload={
                        "wallet_id": str(old_dispatch_id),
                        "balance_change": "0",
                        "escrow_change": str(-order.delivery.amount_due_dispatch),
                    },
                )

                # Remove escrow from vendor
                await producer.publish_message(
                    service="wallet",
                    operation="create_transaction",
                    payload={
                        "wallet_id": str(order.delivery.dispatch_id),
                        "tx_ref": str(uuid.uuid4()),
                        "amount": str(-order.delivery.amount_due_dispatch),
                        "transaction_type": TransactionType.ORDER_CANCELLATION,
                        "transaction_direction": TransactionDirection.DEBIT,
                        "payment_status": PaymentStatus.PAID,
                        "payment_method": PaymentMethod.FUND_REVERSAL,
                        "from_user": "System Reversal",
                    },
                )

            # Invalidate caches
            cache_keys_to_delete = [
                f"user_orders:{order.owner_id}",
                f"order_details:{order.id}",
                "paid_pending_deliveries",
                ALL_DELIVERY,
                "orders",
                f"delivery:{order.delivery.id}",
                f"user_related_orders:{current_user.id}",
                f"order_by_id:{order.id}",
            ]
            redis_client.delete(*cache_keys_to_delete)

            # Notify customer
            try:
                customer_token = await get_user_notification_token(
                    db=db, user_id=order.owner_id
                )
                if customer_token:
                    await send_push_notification(
                        tokens=[customer_token],
                        title="Delivery Canceled",
                        message=f"The delivery for your order #{order.order_number} was cancelled by the rider/dispatch. It is now available for other riders.",
                        navigate_to="/(app)/delivery/orders",
                    )
            except HTTPException as e:
                if e.status_code == status.HTTP_404_NOT_FOUND:
                    logger.warning(
                        f"Could not send rider cancellation notification: {e.detail}"
                    )
                else:
                    raise

            # Broadcast WebSocket updates
            await ws_service.broadcast_order_status_update(
                order_id=str(order.id), new_status=OrderStatus.PENDING.value
            )
            await ws_service.broadcast_delivery_status_update(
                delivery_id=str(order.delivery.id),
                new_status=DeliveryStatus.PENDING.value,
            )

            return DeliveryStatusUpdateSchema(
                order_status=OrderStatus.PENDING.value,
                delivery_status=DeliveryStatus.PENDING.value,
            )

        # --- Sender/Vendor(Full Cancellation) ---
        else:
            order.cancel_reason = reason.reason
            order.order_status = OrderStatus.CANCELLED
            if order.delivery:
                order.delivery.delivery_status = DeliveryStatus.CANCELLED

            # Process refunds for paid orders
            if order.order_payment_status == PaymentStatus.PAID:
                # Refund buyer and set payment status to refund
                buyer_refund_amount = order.grand_total
                order.order_payment_status = PaymentStatus.PENDING
                await producer.publish_message(
                    service="wallet",
                    operation="update_wallet",
                    payload={
                        "wallet_id": str(order.owner_id),
                        "balance_change": str(buyer_refund_amount),
                        "escrow_change": str(-buyer_refund_amount),
                    },
                )

                # Deduct from vendor escrow
                await producer.publish_message(
                    service="wallet",
                    operation="update_wallet",
                    payload={
                        "wallet_id": str(order.vendor_id),
                        "balance_change": "0",
                        "escrow_change": str(-order.amount_due_vendor),
                    },
                )

                # Deduct from dispatch escrow if applicable
                if order.delivery and order.delivery.dispatch_id:
                    await producer.publish_message(
                        service="wallet",
                        operation="update_wallet",
                        payload={
                            "wallet_id": str(order.delivery.dispatch_id),
                            "balance_change": "0",
                            "escrow_change": str(-order.delivery.amount_due_dispatch),
                        },
                    )

                # Create a refund transaction record
                await producer.publish_message(
                    service="wallet",
                    operation="create_transaction",
                    payload={
                        "wallet_id": str(order.owner_id),
                        "tx_ref": str(uuid.uuid4()),
                        "amount": str(buyer_refund_amount),
                        "transaction_type": TransactionType.REFUND,
                        "transaction_direction": TransactionDirection.CREDIT,
                        "payment_status": PaymentStatus.PAID,
                        "payment_method": PaymentMethod.SYSTEM_REFUND,
                        "from_user": "System",
                    },
                )

            # Invalidate caches
            cache_keys_to_delete = [
                f"user_orders:{order.owner_id}",
                f"user_orders:{order.vendor_id}",
                f"order_details:{order.id}",
                "paid_pending_deliveries",
                f"order_by_id:{order.id}",
                ALL_DELIVERY,
                "orders",
            ]
            if order.delivery:
                cache_keys_to_delete.append(f"delivery:{order.delivery.id}")
                if order.delivery.rider_id:
                    cache_keys_to_delete.append(
                        f"user_related_orders:{order.delivery.rider_id}"
                    )

            redis_client.delete(*cache_keys_to_delete)

            # Send notifications
            try:
                if current_user.id == order.owner_id:
                    # Notify vendor
                    vendor_token = await get_user_notification_token(
                        db=db, user_id=order.vendor_id
                    )
                    if vendor_token:
                        await send_push_notification(
                            tokens=[vendor_token],
                            title="Order Canceled",
                            message=f"Order #{order.order_number} has been cancelled by the customer.",
                            navigate_to="/(app)/delivery/orders",
                        )
                elif current_user.id == order.vendor_id:
                    # Notify customer
                    customer_token = await get_user_notification_token(
                        db=db, user_id=order.owner_id
                    )
                    if customer_token:
                        await send_push_notification(
                            tokens=[customer_token],
                            title="Order Canceled",
                            message=f"Your order #{order.order_number} has been cancelled by the vendor.",
                            navigate_to="/(app)/delivery/orders",
                        )
            except HTTPException as e:
                if e.status_code == 404 and "Notification token not found" in e.detail:
                    logger.warning(
                        f"Could not send cancellation notification: {e.detail}"
                    )
                else:
                    raise

            # Broadcast WebSocket updates
            await ws_service.broadcast_order_status_update(
                order_id=str(order.id), new_status=OrderStatus.CANCELLED
            )
            if order.delivery:
                await ws_service.broadcast_delivery_status_update(
                    delivery_id=str(order.delivery.id),
                    new_status=DeliveryStatus.CANCELLED,
                )

            return DeliveryStatusUpdateSchema(
                order_status=OrderStatus.CANCELLED,
                delivery_status=DeliveryStatus.CANCELLED if order.delivery else None,
            )


async def re_list_item_for_delivery(
    db: AsyncSession, delivery_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Re-lists a delivery for pickup after a rider has cancelled.
    This action can only be performed by the order owner.
    """
    async with db.begin():
        # 1. Fetch the delivery and its order, and lock the row for update.
        stmt = (
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .options(selectinload(Delivery.order))
            .with_for_update()
        )
        result = await db.execute(stmt)
        delivery = result.scalar_one_or_none()

        if not delivery:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found."
            )

        order = delivery.order
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Associated order not found.",
            )

        # 2. Authorization and State Validation
        if order.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to re-list this delivery.",
            )

        if delivery.delivery_status != DeliveryStatus.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only a cancelled delivery can be re-listed.",
            )

        # 3. Update the delivery to be available again
        delivery.delivery_status = DeliveryStatus.PENDING
        delivery.rider_id = None
        delivery.dispatch_id = None
        delivery.rider_phone_number = None

        # Also update the main order status if it was also cancelled
        if order.order_status == OrderStatus.CANCELLED:
            order.order_status = OrderStatus.PENDING

        db.add(delivery)
        db.add(order)

        await producer.publish_message(
            service="wallet",
            operation="update_wallet",
            payload={
                "wallet_id": str(order.owner_id),
                "balance_change": str(-delivery.delivery_fee),
                "escrow_change": str(delivery.delivery_fee),
            },
        )

        # Create a refund transaction record
        await producer.publish_message(
            service="wallet",
            operation="create_transaction",
            payload={
                "wallet_id": str(order.owner_id),
                "tx_ref": str(order.tx_ref),
                "amount": str(delivery.delivery_fee),
                "transaction_type": TransactionType.USER_TO_USER,
                "transaction_direction": TransactionDirection.DEBIT,
                "payment_status": PaymentStatus.PAID,
                "payment_method": PaymentMethod.WALLET,
                "from_user": "Self",
            },
        )

        # 4. Invalidate Caches
        invalidate_delivery_cache(delivery.id)
        redis_client.delete(ALL_DELIVERY)
        redis_client.delete("paid_pending_deliveries")
        redis_client.delete(f"user_related_orders:{current_user.id}")
        redis_client.delete(f"order_by_id:{order.id}")
        if order.vendor_id:
            redis_client.delete(f"user_related_orders:{order.vendor_id}")

        # 5. Notifications and Broadcasts
        if order.vendor_id and order.vendor_id != order.owner_id:
            try:
                vendor_token = await get_user_notification_token(
                    db=db, user_id=order.vendor_id
                )
                if vendor_token:
                    await send_push_notification(
                        tokens=[vendor_token],
                        title="Delivery Re-listed",
                        message=f"The delivery for order #{order.order_number} has been re-listed and is now awaiting a new rider.",
                        navigate_to="/(app)/delivery/orders",
                    )
            except HTTPException as e:
                if e.status_code == 404:
                    logger.warning(f"Could not send re-list notification: {e.detail}")
                else:
                    raise

        await ws_service.broadcast_delivery_status_update(
            delivery_id=str(delivery.id), new_status=DeliveryStatus.PENDING.value
        )

        return DeliveryStatusUpdateSchema(
            delivery_status=delivery.delivery_status, order_status=order.order_status
        )


# For orders without delivery
async def vendor_mark_order_delivered(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Mark an order as delivered by the vendor. This is specifically for pickup orders
    without delivery service.

    Args:
        db: Database session
        order_id: UUID of the order to mark as delivered
        current_user: The vendor marking the order as delivered

    Returns:
        DeliveryStatusUpdateSchema with updated order status

    Raises:
        HTTPException: With appropriate status code and message for various failure cases
    """
    try:
        # Fetch order with lock
        order_result = await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .where(Order.vendor_id == current_user.id)
            .options(selectinload(Order.owner))
            .with_for_update()
        )
        order = order_result.scalar_one_or_none()

        # Comprehensive validation
        if not order:
            logger.error(f"Order {order_id} not found for vendor {current_user.id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found or has been deleted.",
            )

        # Authorization validation
        if order.vendor_id != current_user.id:
            logger.warning(
                f"Unauthorized order status update attempt by vendor {current_user.id} "
                f"for order {order_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to update this order's status.",
            )

        # Order type validation
        # if order.require_delivery != RequireDeliverySchema.PICKUP:
        #     raise HTTPException(
        #         status_code=status.HTTP_400_BAD_REQUEST,
        #         detail="This operation is only valid for pickup orders.",
        #     )

        # Status transition validation
        if order.order_status == OrderStatus.DELIVERED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This order has already been marked as delivered.",
            )

        if order.order_status not in [OrderStatus.PENDING, OrderStatus.ACCEPTED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot mark as delivered. Current status: {order.order_status.value}",
            )

        # Payment validation
        if order.order_payment_status != PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot mark as delivered: Order payment is not completed.",
            )

        order.order_status = OrderStatus.DELIVERED
        await db.commit()

        # Send notifications to all stakeholders
        try:
            await _notify_order_pickup_delivered(order, db)
        except Exception as e:
            logger.error(
                f"Failed to send notifications for order {order_id}: {str(e)}",
                exc_info=True,
            )

        # Invalidate caches
        try:
            _invalidate_pickup_order_caches(order, current_user)
        except Exception as e:
            logger.error(
                f"Failed to invalidate caches for order {order_id}: {str(e)}",
                exc_info=True,
            )
         
        # Broadcast status update
        await ws_service.broadcast_order_status_update(
            order_id=order.id, new_status=order.order_status
        )

        redis_client.delete(f"order_by_id:{order_id}")
        logger.info(
            f"Successfully marked order {order_id} as delivered by vendor {current_user.id}"
        )

        return DeliveryStatusUpdateSchema(order_status=order.order_status)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(
            f"Unexpected error marking order {order_id} as delivered: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating order status.",
        )


async def _notify_order_pickup_delivered(order: Order, db: AsyncSession):
    """
    Send notifications to all stakeholders about order pickup delivery completion.
    Implements retry logic for notification failures.
    """
    MAX_RETRIES = 3
    stakeholders = {"owner": order.owner_id, "vendor": order.vendor_id}

    for role, user_id in stakeholders.items():
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                token = await get_user_notification_token(db=db, user_id=user_id)
                if token:
                    message = {
                        "owner": "Your order has been marked as delivered by the vendor. Please verify before marking as received.",
                        "vendor": "You have successfully marked the order as delivered.",
                    }

                    await send_push_notification(
                        tokens=[token],
                        title="Order Update",
                        message=message[role],
                        navigate_to="/(app)/orders",
                    )
                break
            except Exception as e:
                retry_count += 1
                if retry_count == MAX_RETRIES:
                    logger.error(
                        f"Failed to send notification to {role} after {MAX_RETRIES} attempts: {str(e)}",
                        exc_info=True,
                    )
                await asyncio.sleep(1)  # Wait before retry


def _invalidate_pickup_order_caches(order: Order, current_user: User):
    """
    Invalidate all relevant caches related to a pickup order.
    Handles each cache operation separately to prevent total failure.
    """
    cache_keys = [
        f"user_related_orders:{current_user.id}",
        f"user_related_orders:{order.owner_id}",
        f"user_related_orders:{order.vendor_id}",
        f"order:{order.id}",
        f"order_by_id: {order.id}" "pending_orders",
        "orders",
    ]

    for key in cache_keys:
        try:
            redis_client.delete(key)
        except Exception as e:
            logger.error(f"Failed to invalidate cache key {key}: {str(e)}")
            continue  # Continue with other cache invalidations


async def _validate_delivery_acceptance(
    db: AsyncSession,
    order_id: UUID,
    rider: User,
    delivery_status: DeliveryStatus,
    order_status: OrderStatus,
) -> Order:
    """Validates all preconditions for a rider to accept a delivery."""
    if rider.user_type != UserType.RIDER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a rider can accept orders.",
        )

    if (
        rider.profile.profile_image is None
        or not rider.profile.profile_image.profile_image_url
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profile image is missing. Please update your profile.",
        )

    if rider.rider_is_suspended_for_order_cancel:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is suspended due to too many cancellations.",
        )

    # Fetch and lock the order to prevent race conditions
    order = await db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.delivery))
        .with_for_update()
    )

    if not order or not order.delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found."
        )

    if order.delivery.rider_id != rider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid user",
        )
    
    if order.delivery.delivery_status != DeliveryStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This delivery has already been accepted or is no longer available.",
        )

    order.order_status = order_status
    order.delivery.delivery_status = delivery_status

    return order


async def _assign_rider_and_update_db(
    db: AsyncSession,
    order_id: UUID,  # Pass IDs instead of result rows
    delivery_id: UUID,
    rider_id: UUID,
):
    """Atomically updates the database to assign the rider and update statuses."""
    # Fetch rider information
    stmt = (
        select(User.id, User.dispatcher_id, Profile.phone_number)
        .join(Profile)
        .where(User.id == rider_id)
    )
    result = await db.execute(stmt)
    rider = result.first()

    if not rider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rider not found.",
        )

    _rider_id, dispatcher_id, phone_number = rider

    # Update the delivery record
    await db.execute(
        update(Delivery)
        .where(Delivery.id == delivery_id)
        .values(
            rider_id=_rider_id,
            dispatch_id=dispatcher_id,
            rider_phone_number=phone_number,
        )
    )

    # Update the rider's has_delivery status
    await db.execute(update(User).where(User.id == rider_id).values(has_delivery=True))


async def _decline_delivery_order_and_update_db(
    db: AsyncSession, order: Order, rider_id: UUID
):
    """Atomically updates the database to assign the rider and update statuses."""

    stmt = select(User.id).where(User.id == rider_id)
    result = await db.execute(stmt)
    rider = result.first()

    if not rider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rider not found.",
        )

    order.delivery.rider_id = None
    order.delivery.dispatch_id = None
    order.delivery.rider_phone_number = None
    order.order_status = OrderStatus.CANCELLED
    order.delivery.delivery_status = DeliveryStatus.CANCELLED
    # rider.has_delivery = False
    db.add(order)
    db.add(order.delivery)

    await db.execute(
        update(User).where(User.id == rider.id).values(has_delivery=False)
    )


async def _rider_pickup_and_update_db(
    db: AsyncSession, order: Order, rider: User, dispatch_id: UUID
):
    """Atomically updates the database to assign the rider and update statuses."""
    order.delivery.delivery_status = DeliveryStatus.PICKED_UP
    
    db.add(order)
    db.add(order.delivery)
    await db.refresh(order)
    


async def _dispatch_post_pickup_tasks(order: Order, rider: User, db: AsyncSession):
    """
    Handles tasks that should occur after the database transaction is committed.
    Uses idempotency keys to prevent duplicate event processing.
    """
    dispatch_profile = await get_user_profile(order.delivery.dispatch_id, db=db)
    sender_profile = await get_user_profile(order.owner_id, db=db)

    # Use a unique idempotency key based on the order and action
    idempotency_key = f"pickup:{order.id}:{order.delivery.id}"

    # Check if we've already processed this pickup
    cache_key = f"idempotency:{idempotency_key}"
    if redis_client.get(cache_key):
        logger.info(
            f"Pickup for order {order.id} already processed. Skipping post-pickup tasks."
        )
        return

    # Set idempotency marker (expires after 24 hours as a safety measure)
    redis_client.setex(cache_key, 86400, "1")


    # Update sender transaction
    await producer.publish_message(
        service="wallet",
        operation="update_transaction",
        payload={
            "idempotency_key": f"{idempotency_key}:update_sender_tx",
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
            "idempotency_key": f"{idempotency_key}:update_dispatch_escrow",
            "wallet_id": str(order.delivery.dispatch_id),
            "balance_change": "0",
            "transaction_direction": TransactionDirection.CREDIT,
            "escrow_change": str(order.delivery.delivery_fee),
        },
    )

    # 2. Invalidate Caches
    keys_to_delete = {
        f"user_related_orders:{rider.id}",
        f"user_related_orders:{order.delivery.dispatch_id}",
        f"user_related_orders:{order.delivery.rider_id}",
        f"user_orders:{order.owner_id}",
        f"user_orders:{order.delivery.rider_id}",
        f"user_orders:{order.delivery.dispatch_id}",
        f"delivery:{order.delivery.id}",
        f"order_by_id:{order.id}",
        ALL_DELIVERY,
        "paid_pending_deliveries",
    }
    redis_client.delete(*keys_to_delete)

    # 3. Broadcast WebSocket updates (these are typically idempotent by nature)
    await ws_service.broadcast_delivery_status_update(
        delivery_id=order.delivery.id, new_status=DeliveryStatus.PICKED_UP
    )
    await ws_service.broadcast_order_status_update(
        order_id=order.id, new_status=OrderStatus.ACCEPTED
    )

    # 4. Send Push Notification (idempotent - multiple notifications are acceptable)
    sender_token = await get_user_notification_token(db=db, user_id=order.owner_id)
    if sender_token:
        await send_push_notification(
            tokens=[sender_token],
            title="Order Assigned",
            message=f"Your order has been assigned to {rider.profile.full_name}, {rider.profile.phone_number}",
            navigate_to="/(app)/delivery/orders",
        )



async def rider_accept_booking(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Allows a rider to accept a delivery order.
    Note: Funds are NOT moved to escrow yet - this happens at pickup.
    """
    # Add endpoint-level idempotency
    endpoint_idempotency_key = f"rider_accept:{order_id}:{current_user.id}"
    cache_key = f"idempotency:{endpoint_idempotency_key}"
    
    is_first_call = redis_client.setnx(cache_key, "processing")
    
    if not is_first_call:
        existing_status = redis_client.get(cache_key)
        if existing_status == b"completed":
            logger.info(f"Rider acceptance for order {order_id} already processed.")
            order = await db.scalar(
                select(Order)
                .where(Order.id == order_id)
                .options(selectinload(Order.delivery))
            )
            if order:
                return DeliveryStatusUpdateSchema(
                    delivery_status=order.delivery.delivery_status
                )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This delivery acceptance is already being processed."
        )
    
    redis_client.expire(cache_key, 300)
    
    try:
        order = await _validate_delivery_acceptance(
            db,
            order_id,
            current_user,
            order_status=OrderStatus.ACCEPTED,
            delivery_status=DeliveryStatus.ACCEPTED,
        )
        await db.commit()

        logger.info(f"Rider accepted order {order_id}. Funds will move to escrow at pickup.")

        _invalidate_order_caches(order=order, current_user=current_user)
        redis_client.delete(f"order_by_id:{order_id}")
        
        redis_client.setex(cache_key, 86400, "completed")

        return DeliveryStatusUpdateSchema(
            delivery_status=order.delivery.delivery_status
        )

    except HTTPException:
        await db.rollback()
        redis_client.delete(cache_key)
        raise
    except Exception as e:
        await db.rollback()
        redis_client.delete(cache_key)
        logger.error(
            f"Failed to accept delivery for order {order_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while accepting the delivery.",
        )




async def _update_wallet_at_pickup(order: Order):
    """
    
    Process wallet settlements for package delivery at PICKUP stage.
    Put FULL delivery_fee into dispatch escrow when rider picks up the package.
    
    Flow:
    1. Rider accepts booking → No wallet changes
    2. Rider picks up package → THIS FUNCTION RUNS → Delivery fee goes to dispatch escrow
    3. Sender confirms received → Settlement function runs → Move to balance
    
    Args:
        order: Order instance with loaded delivery relationship

    Raises:
        HTTPException: If wallet operations fail after retries
    """
    idempotency_key = f"pickup_escrow:{order.id}:{order.delivery.id}"  # Changed key prefix
    cache_key = f"idempotency:{idempotency_key}"
    
    # Atomic check-and-set using SETNX
    is_first_call = redis_client.setnx(cache_key, "processing")
    
    if not is_first_call:
        existing_status = redis_client.get(cache_key)
        if existing_status == b"completed":
            logger.info(f"Pickup escrow for order {order.id} already completed.")
            return True
        elif existing_status == b"processing":
            # Wait for concurrent process to complete
            for i in range(10):
                await asyncio.sleep(1)
                status = redis_client.get(cache_key)
                if status == b"completed":
                    logger.info(f"Pickup escrow for order {order.id} completed by concurrent request.")
                    return True
            logger.warning(f"Timeout waiting for concurrent escrow processing for order {order.id}")
            return True
    
    # Set expiry on the processing lock (5 minutes)
    redis_client.expire(cache_key, 300)

    MAX_RETRIES = 3
    retry_count = 0
    
    while retry_count < MAX_RETRIES:
        try:
            dispatch_amount = order.delivery.amount_due_dispatch
            delivery_fee = order.delivery.delivery_fee

            # Validate amounts
            if dispatch_amount < 0 or delivery_fee < 0:
                redis_client.delete(cache_key)
                logger.error(f"Invalid amounts for order {order.id}: dispatch_amount={dispatch_amount}, delivery_fee={delivery_fee}")
                raise ValueError("Settlement amounts cannot be negative")
            
            if dispatch_amount > delivery_fee:
                redis_client.delete(cache_key)
                logger.error(f"Invalid amounts for order {order.id}: dispatch_amount={dispatch_amount} > delivery_fee={delivery_fee}")
                raise ValueError("Dispatch amount cannot exceed delivery fee")

            # Validate order is in correct state
            if order.delivery.delivery_status != DeliveryStatus.PICKED_UP:
                redis_client.delete(cache_key)
                logger.error(f"Invalid status for escrow allocation: {order.delivery.delivery_status}")
                raise ValueError("Can only allocate escrow when order is picked up")

            # Update dispatch company wallet - put FULL delivery_fee into escrow
            logger.info(f"Allocating {delivery_fee} to dispatch escrow for order {order.id}")
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.delivery.dispatch_id),
                    "balance_change": "0",
                    "escrow_change": str(delivery_fee),
                    "idempotency_key": idempotency_key,
                    "details": {
                        "order_id": str(order.id),
                        "operation": "pickup_escrow_allocation",
                        "order_number": order.order_number,
                        "delivery_fee": str(delivery_fee),
                        "amount_due_dispatch": str(dispatch_amount),
                        "stage": "pickup",
                    },
                },
            )

            # Mark as completed (expires after 24 hours)
            redis_client.setex(cache_key, 86400, "completed")
            
            logger.info(
                f"Pickup escrow allocation completed for order {order.id}: "
                f"delivery_fee={delivery_fee} added to dispatch escrow at PICKUP stage"
            )
            return True

        except ValueError as ve:
            redis_client.delete(cache_key)
            logger.error(f"Validation error for order {order.id}: {str(ve)}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid order data: {str(ve)}",
            )
            
        except Exception as e:
            retry_count += 1
            logger.error(
                f"Pickup escrow allocation attempt {retry_count} failed for order {order.id}: {str(e)}",
                exc_info=True,
            )
            
            if retry_count >= MAX_RETRIES:
                redis_client.delete(cache_key)
                logger.error(
                    f"All {MAX_RETRIES} attempts failed for order {order.id}. Final error: {str(e)}"
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to allocate pickup escrow after {MAX_RETRIES} attempts: {str(e)}",
                )
            
            # Exponential backoff
            await asyncio.sleep(2 ** retry_count)

    redis_client.delete(cache_key)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected error in pickup escrow processing",
    )

async def rider_decline_booking(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Allows a rider to decline a delivery order.
    """
    try:
        # 1. Fetch the order with delivery
        result = await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.delivery))
        )
        order = result.scalar_one()

        # 2. Check if rider owns it
        if order.delivery.rider_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid rider"
            )

        # 3. Decline it
        await _decline_delivery_order_and_update_db(
            db=db, order=order, rider_id=current_user.id
        )
        await db.commit()

        # 4. Invalidate cache
        _invalidate_order_caches(order, current_user)

        return DeliveryStatusUpdateSchema(
            delivery_status=order.delivery.delivery_status
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(
            f"Failed to decline delivery for order {order_id}: {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred",
        )

async def assign_rider_to_existing_delivery_order(
    db: AsyncSession, 
    delivery_id: UUID, 
    rider_id: UUID
) -> DeliveryStatusUpdateSchema:
    # --- 1. Load delivery + order ---
    delivery_stmt = (
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .options(selectinload(Delivery.order))
        .with_for_update()
    )
    result = await db.execute(delivery_stmt)
    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found")

    if not delivery.order:
        raise HTTPException(status_code=400, detail="Delivery has no associated order")

    # --- 2. Load rider ---
    rider = await get_user_profile(db=db, user_id=rider_id)
    if not rider:
        raise HTTPException(status_code=404, detail="Rider not found")

    try:
        # --- 3. Update Delivery ---
        delivery.rider_id = rider.user_id
        delivery.dispatch_id = rider.user.dispatcher_id
        delivery.rider_phone_number = rider.phone_number
        delivery.delivery_status = DeliveryStatus.PENDING

        # --- 4. Update Order status ---
        delivery.order.order_status = OrderStatus.PENDING

        # --- 5. Update Rider has_delivery ---
        await db.execute(
            update(User)
            .where(User.id == rider_id)
            .values(has_delivery=True)
        )

        # --- 6. Commit ---
        await db.commit()
        await db.refresh(delivery)

        redis_client.delete("near_by_riders")
        redis_client.delete(f"order_by_id:{delivery.order.id}")

        return DeliveryStatusUpdateSchema(order_status=delivery.order.order_status, delivery_status=delivery.delivery_status)
    except Exception as e:
        logger.error(f'Error assigning a rider {e}')
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error assigning a rider")




async def rider_pickup_delivery_order(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Allows a rider to pickup a delivery order from customer.
    THIS is where funds move to dispatch escrow.
    """
    # Add endpoint-level idempotency FIRST
    endpoint_idempotency_key = f"rider_pickup:{order_id}:{current_user.id}"
    cache_key = f"idempotency:{endpoint_idempotency_key}"
    
    is_first_call = redis_client.setnx(cache_key, "processing")
    
    if not is_first_call:
        existing_status = redis_client.get(cache_key)
        if existing_status == b"completed":
            logger.info(f"Rider pickup for order {order_id} already processed.")
            order = await db.scalar(
                select(Order)
                .where(Order.id == order_id)
                .options(selectinload(Order.delivery))
            )
            if order:
                return DeliveryStatusUpdateSchema(
                    delivery_status=order.delivery.delivery_status
                )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This pickup is already being processed."
        )
    
    redis_client.expire(cache_key, 300)
    
    try:
        order = await db.scalar(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.delivery))
            .with_for_update()
        )

        if not order:
            redis_client.delete(cache_key)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
            )

        if current_user.id != order.delivery.rider_id:
            redis_client.delete(cache_key)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid rider."
            )

        # Check if already picked up (idempotency at DB level)
        if order.delivery.delivery_status == DeliveryStatus.PICKED_UP:
            logger.info(
                f"Order {order_id} already picked up. Returning current status."
            )
            redis_client.setex(cache_key, 86400, "completed")
            return DeliveryStatusUpdateSchema(
                delivery_status=order.delivery.delivery_status
            )

        # Validate status transition
        if order.delivery.delivery_status != DeliveryStatus.ACCEPTED:
            redis_client.delete(cache_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot pickup. Current status: {order.delivery.delivery_status.value}. Must be ACCEPTED."
            )

        # Update status FIRST
        await _rider_pickup_and_update_db(
            db, order, current_user, dispatch_id=current_user.dispatcher_id
        )
        
        await db.commit()

        logger.info(f"Pickup confirmed. Moving funds to escrow for order {order_id}")
        await _update_wallet_at_pickup(order)
        logger.info(f"Escrow allocation completed for order {order_id}")

        # Post-pickup tasks
        # await _dispatch_post_pickup_tasks(order, current_user, db)
        _invalidate_order_caches(order, current_user)
        redis_client.delete(f"order_by_id:{order_id}")
        
        # Mark as completed
        redis_client.setex(cache_key, 86400, "completed")

        return DeliveryStatusUpdateSchema(
            delivery_status=order.delivery.delivery_status
        )

    except HTTPException:
        await db.rollback()
        redis_client.delete(cache_key)
        raise
    except Exception as e:
        await db.rollback()
        redis_client.delete(cache_key)
        logger.error(
            f"Failed to pickup delivery for order {order_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the pickup.",
        )

async def laundry_pickup(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Laundry vendor pickup laundry from client
    """

    result = await db.execute(
        select(Order).where(Order.id == order_id).with_for_update()
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )

    order.order_status = OrderStatus.VENDOR_PICKUP_LAUNDRY
    await db.commit()
    await db.refresh(order)


    # Vendor wallet update (add full amount to escrow)
    await producer.publish_message(
        service="wallet",
        operation="update_wallet",
        payload={
            "wallet_id": str(order.vendor_id),
            "escrow_change": str(order.grand_total),
            "balance_change": str(0),
        },
    )

    _invalidate_order_caches(order, current_user)
    redis_client.delete(f"order_by_id:{order_id}")


    await ws_service.broadcast_order_status_update(
        order_id=order.id, new_status=order.order_status
    )

    sender_token = await get_user_notification_token(db=db, user_id=order.owner_id)

    if sender_token:
        await send_push_notification(
            tokens=[sender_token],
            title="Vendor Received Laundry",
            message=f"Laundry picked up by vendor. {current_user.profile.business_name}, {current_user.profile.phone_number}",
            navigate_to="/(app)/delivery/orders",
        )

    return DeliveryStatusUpdateSchema(order_status=order.order_status)


async def laundry_returned(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .where(Order.vendor_id == current_user.id)
        .with_for_update()
    )
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found."
        )
    if order.order_status != OrderStatus.VENDOR_PICKUP_LAUNDRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can only return an order that is in vendor received state.",
        )

    order.order_status = OrderStatus.VENDOR_RETURNED_LAUNDRY
    await db.commit()
    await db.refresh(order)

    _invalidate_order_caches(order, current_user)

    await ws_service.broadcast_order_status_update(
        order_id=order.id, new_status=order.order_status
    )

    sender_token = await get_user_notification_token(db=db, user_id=order.owner_id)

    if sender_token:
        await send_push_notification(
            tokens=[sender_token],
            title="Order Delivered",
            message=f"Laundry returned by vendor. Please confirm before marking as received.",
            navigate_to="/(app)/delivery/orders",
        )

    return DeliveryStatusUpdateSchema(order_status=order.order_status)


async def _validate_delivery(order: Order, current_user: User):
    """
    Validates a delivery for confirmation with comprehensive error checking.

    Args:
        order: Order instance to validate
        current_user: User attempting to confirm the delivery

    Raises:
        HTTPException: With appropriate status code and detailed error message
            - 404: Order not found
            - 403: Unauthorized access
            - 400: Invalid order state or business logic violation
    """
    try:
        # 1. Existence check
        if not order:
            logger.error(f"Attempted to validate non-existent order")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
            )

        # 2. Authorization check
        if order.owner_id != current_user.id:
            logger.warning(
                f"Unauthorized confirmation attempt: User {current_user.id} tried to confirm "
                f"order {order.id} owned by {order.owner_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to confirm this delivery",
            )

        # 3. Transaction reference check
        if not order.tx_ref:
            logger.error(f"Order {order.id} missing transaction reference")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This order cannot be confirmed - missing transaction reference. "
                    "Please contact support."
                ),
            )

        # 4. Delivery existence check
        if not order.delivery:
            logger.error(f"Order {order.id} has no associated delivery")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This order has no associated delivery record",
            )

        # 5. Payment status check
        if order.order_payment_status != PaymentStatus.PAID:
            logger.warning(f"Attempted to confirm unpaid delivery for order {order.id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot confirm delivery - payment is not complete",
            )

        # 6. Delivery status validation
        if order.delivery.delivery_status == DeliveryStatus.RECEIVED:
            logger.warning(f"Duplicate confirmation attempt for order {order.id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This delivery has already been confirmed as received",
            )

        if order.delivery.delivery_status != DeliveryStatus.DELIVERED:
            logger.warning(
                f"Invalid status transition attempt for order {order.id}: "
                f"from {order.delivery.delivery_status} to RECEIVED"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot confirm delivery - package must be marked as delivered "
                    "by the rider first"
                ),
            )

        logger.info(f"Delivery validation successful for order {order.id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error validating delivery for order: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while validating the delivery",
        )


async def _update_order_status(order: Order, db: AsyncSession, status: OrderStatus):
    """
    Update an order's status with validation and audit logging.

    Args:
        order: Order instance to update
        db: Database session
        status: New OrderStatus value

    Raises:
        HTTPException: If update fails
    """
    try:
        old_status = order.order_status
        order.order_status = status
        db.add(order)
        await db.commit()

        logger.info(f"Order {order.id} status updated: {old_status} -> {status}")

    except Exception as e:
        logger.error(
            f"Failed to update status for order {order.id}: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update order status",
        )


async def _update_delivery_status(
    order: Order,
    db: AsyncSession,
    order_status: OrderStatus,
    delivery_status: DeliveryStatus,
):
    """
    Update both order and delivery status atomically with validation and audit logging.

    Args:
        order: Order instance with delivery relationship
        db: Database session
        order_status: New OrderStatus value
        delivery_status: New DeliveryStatus value

    Raises:
        HTTPException: If validation fails or update fails
    """
    try:
        # Store old values for audit
        old_order_status = order.order_status
        old_delivery_status = order.delivery.delivery_status

        # Validate status transition
        valid_transitions = {
            DeliveryStatus.DELIVERED: [DeliveryStatus.RECEIVED],
            DeliveryStatus.ACCEPTED: [
                DeliveryStatus.PICKED_UP,
                DeliveryStatus.CANCELLED,
            ],
            DeliveryStatus.PICKED_UP: [
                DeliveryStatus.DELIVERED,
                DeliveryStatus.CANCELLED,
            ],
        }

        current_status = order.delivery.delivery_status
        if (
            current_status in valid_transitions
            and delivery_status not in valid_transitions[current_status]
        ):
            raise ValueError(
                f"Invalid status transition from {current_status} to {delivery_status}"
            )

        # Update statuses
        order.order_status = order_status
        order.delivery.delivery_status = delivery_status
        db.add(order)
        db.add(order.delivery)
        await db.commit()

        logger.info(
            f"Updated order {order.id} delivery status: "
            f"{old_delivery_status} -> {delivery_status}"
        )

    except ValueError as e:
        logger.warning(f"Invalid status transition for order {order.id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(
            f"Failed to update delivery status for order {order.id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update delivery status",
        )


async def _send_notifications(order: Order, db: AsyncSession):
    """
    Send comprehensive notifications to all relevant parties about order/delivery completion.

    Args:
        order: Order instance with loaded delivery relationship
        db: Database session

    Handles:
    - WebSocket status updates
    - Push notifications to all involved parties
    - Success/failure logging
    """
    try:
        # 1. Broadcast WebSocket updates
        await ws_service.broadcast_delivery_status_update(
            delivery_id=str(order.delivery.id),
            new_status=order.delivery.delivery_status.value,
        )
        await ws_service.broadcast_order_status_update(
            order_id=str(order.id), new_status=order.order_status.value
        )

        # 2. Notify rider
        if order.delivery.rider_id:
            rider_token = await get_user_notification_token(
                db=db, user_id=order.delivery.rider_id
            )
            if rider_token:
                await send_push_notification(
                    tokens=[rider_token],
                    title="Delivery Completed",
                    message=(
                        f"Delivery for order #{order.order_number} has been confirmed. "
                        "Your payment has been processed."
                    ),
                    navigate_to="/(app)/delivery/orders",
                )

        # 3. Notify dispatch company
        if order.delivery.dispatch_id:
            dispatch_token = await get_user_notification_token(
                db=db, user_id=order.delivery.dispatch_id
            )
            if dispatch_token:
                await send_push_notification(
                    tokens=[dispatch_token],
                    title="Delivery Confirmed",
                    message=(
                        f"Delivery for order #{order.order_number} has been confirmed. "
                        "Payment has been credited to your wallet."
                    ),
                    navigate_to="/(app)/delivery/orders",
                )

        # 4. Notify vendor (if applicable)
        if order.vendor_id and order.vendor_id != order.owner_id:
            vendor_token = await get_user_notification_token(
                db=db, user_id=order.vendor_id
            )
            if vendor_token:
                await send_push_notification(
                    tokens=[vendor_token],
                    title="Order Completed",
                    message=(
                        f"Order #{order.order_number} has been completed and confirmed "
                        "by the customer."
                    ),
                    navigate_to="/delivery/orders",
                )

        logger.info(f"Successfully sent completion notifications for order {order.id}")

    except Exception as e:
        logger.error(
            f"Failed to send some notifications for order {order.id}: {str(e)}",
            exc_info=True,
        )
        # Don't raise exception - notifications shouldn't block the main flow


def _invalidate_caches(order: Order, current_user: User):
    """
    Invalidate all relevant caches after a delivery confirmation.
    Uses batch deletion where possible and handles Redis errors gracefully.

    Args:
        order: Order instance with loaded relationships
        current_user: User who confirmed the delivery

    Note:
        Failures are logged but do not halt execution since cache invalidation
        is not critical to data consistency (cache will expire naturally)
    """
    try:
        # Collect all keys to invalidate
        cache_keys = [
            f"{ALL_DELIVERY}",
            "paid_pending_deliveries",
            f"user_related_orders:{current_user.id}",
            f"user_orders:{current_user.id}",
            f"order_details:{order.id}",
            f"delivery:{order.delivery.id}",
            f"order_by_id:{order.id}",
            f"near_by_riders",
             redis_client.delete(f'wallet_transactions:{current_user.id}')
        ]

        # Add vendor-related caches if exists
        if order.vendor_id:
            cache_keys.extend(
                [
                    f"user_related_orders:{order.vendor_id}",
                    f"vendor_orders:{order.vendor_id}",
                     redis_client.delete(f'wallet_transactions:{order.vendor_id}')
                ]
            )

        # Add dispatch-related caches
        if order.delivery.dispatch_id:
            cache_keys.extend(
                [
                    f"user_related_orders:{order.delivery.dispatch_id}",
                    f"dispatch_orders:{order.delivery.dispatch_id}",
                     redis_client.delete(f'wallet_transactions:{order.delivery.dispatch_id}')
                ]
            )

        # Add rider-related caches
        if order.delivery.rider_id:
            cache_keys.extend(
                [
                    f"user_related_orders:{order.delivery.rider_id}",
                    f"rider_orders:{order.delivery.rider_id}",
                ]
            )

        # Batch delete all keys
        deleted = redis_client.delete(*cache_keys)

        logger.info(
            f"Successfully invalidated {deleted} cache keys for order {order.id}"
        )

    except Exception as e:
        logger.warning(
            f"Failed to invalidate some caches for order {order.id}: {str(e)}. "
            "This is non-critical and caches will expire naturally.",
            exc_info=True,
        )


#  Customer mark Package received
async def sender_confirm_package_received(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Process package delivery confirmation OR return confirmation for cancelled orders.

    This function handles TWO scenarios:
    1. Normal delivery: Package was delivered and sender confirms receipt
    2. Cancelled after pickup: Rider returned package and sender confirms receipt of return
    
    For cancelled orders that were picked up:
    - Sender is charged the full delivery_fee (stays in escrow, moves to dispatch)
    - Dispatch gets amount_due_dispatch
    - This compensates the rider for picking up and returning the package

    Args:
        db: Database session
        order_id: UUID of the order to confirm
        current_user: User confirming the delivery/return

    Returns:
        DeliveryStatusUpdateSchema with updated status

    Raises:
        HTTPException: With appropriate status code and detailed error message
    """
    try:
        # Start a transaction
        async with db.begin_nested():
            # 1. Fetch order with relationships
            result = await db.execute(
                select(Order)
                .where(Order.id == order_id)
                .options(
                    selectinload(Order.delivery),
                    selectinload(Order.vendor).selectinload(User.profile),
                )
                .with_for_update()
            )
            order = result.scalar_one_or_none()

            # 2. Basic validation
            if not order:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, 
                    detail="Order not found"
                )

            if order.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not authorized to confirm this delivery"
                )

            if not order.delivery:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This order has no associated delivery record"
                )

            # 3. Check if this is a CANCELLED order (return scenario)
            is_cancelled_return = order.order_status == OrderStatus.CANCELLED

            if is_cancelled_return:
                logger.info(
                    f"Processing return confirmation for cancelled order {order.id}"
                )
                
                # Validate it was actually picked up before cancellation
                if order.delivery.delivery_status != DeliveryStatus.CANCELLED:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid order state for return confirmation"
                    )

                # Check if already confirmed
                if order.delivery.delivery_status == DeliveryStatus.RECEIVED:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Return has already been confirmed"
                    )

                # Process return settlement - sender is charged, dispatch is paid
                await _process_cancelled_order_return_settlement(order)

                # Update to RECEIVED status to mark return as confirmed
                order.delivery.delivery_status = DeliveryStatus.RECEIVED
                # Order status stays CANCELLED

                settlement_message = "cancelled order return"
                
            else:
                # Normal delivery flow
                logger.info(
                    f"Processing normal delivery confirmation for order {order.id}"
                )

                # Validate normal delivery state
                await _validate_delivery(order, current_user)

                # Update delivery status
                await _update_delivery_status(
                    order=order,
                    db=db,
                    order_status=OrderStatus.RECEIVED,
                    delivery_status=DeliveryStatus.RECEIVED,
                )

                # Process normal settlement
                try:
                    await _package_settlement(order)
                except Exception as e:
                    logger.error(
                        f"Settlement failed for order {order.id}: {str(e)}", 
                        exc_info=True
                    )
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to process payment settlement. Please contact support.",
                    )

                settlement_message = "delivery"

        # 4. Create audit log (outside nested transaction)
        distance_travelled = Decimal(f"{order.delivery.distance}")
        rider_id = order.delivery.rider_id

        
        await TransactionLogService.create_log(
            db=db,
            vendor_id=current_user.id,
            order_id=order.id,
            amount=order.delivery.delivery_fee - order.delivery.amount_due_dispatch,
            action=TransactionLogAction.RECEIVED,
            status=order.order_payment_status,
            details={
                "order_type": order.order_type,
                "order_number": order.order_number,
                "confirmed_by": current_user.profile.full_name
                or current_user.profile.business_name,
                "phone_number": current_user.profile.phone_number,
                "delivery_fee": str(order.delivery.delivery_fee),
                "amount_due_dispatch": str(order.delivery.amount_due_dispatch),
                "dispatch_id": str(order.delivery.dispatch_id),
                "rider_id": str(order.delivery.rider_id)
                if order.delivery.rider_id
                else None,
                "is_cancelled_return": is_cancelled_return,
            },
        )


        if rider_id:
            # update has_delivery
            await db.execute(
                update(User)
                .where(User.id == rider_id)
                .values(has_delivery=False)
            )
            await db.commit()

            # safe profile update (load-and-mutate)
            stmt = select(Profile).where(Profile.user_id == rider_id).with_for_update()
            result = await db.execute(stmt)
            profile = result.scalar_one_or_none()

            if profile:

                current_distance = profile.total_distance_travelled or Decimal('0.0')
                profile.total_distance_travelled = current_distance + distance_travelled
                db.add(profile)
            else:
                logger.warning(f"Profile not found for rider {rider_id}; cannot update distance.")

        else:
            logger.warning(f"No rider_id on order {order.id}; skipping rider profile updates.")


        await db.execute(
            update(Profile)
            .where(Profile.user_id == order.delivery.rider_id)
            .values(
                total_distance_travelled=Profile.total_distance_travelled + distance_travelled
            )
        )
        
        await db.commit()

        # 6. Post-transaction operations (non-critical)
        try:
            # Send notifications
            if is_cancelled_return:
                await _send_return_confirmation_notifications(order, db)
            else:
                await _send_notifications(order, db)

            # Invalidate caches
            _invalidate_caches(order, current_user)

        except Exception as e:
            logger.warning(
                f"Non-critical post-confirmation operations failed for order {order.id}: {str(e)}",
                exc_info=True,
            )

        # 7. Log successful completion
        logger.info(
            f"Package {settlement_message} confirmation completed successfully for order {order.id}"
        )
        redis_client.delete(f"order_by_id:{order_id}")
        
        return DeliveryStatusUpdateSchema(
            delivery_status=order.delivery.delivery_status,
            order_status=order.order_status,
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(
            f"Failed to confirm package received for order {order_id}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "An unexpected error occurred while confirming the package reception. "
                "Please try again or contact support if the issue persists."
            ),
        )

async def _send_return_confirmation_notifications(order: Order, db: AsyncSession):
    """
    Send notifications when sender confirms receipt of returned package (cancelled order).
    """
    try:
        # 1. Broadcast WebSocket updates
        await ws_service.broadcast_delivery_status_update(
            delivery_id=str(order.delivery.id),
            new_status=order.delivery.delivery_status.value,
        )
        await ws_service.broadcast_order_status_update(
            order_id=str(order.id), 
            new_status=order.order_status.value
        )

        # 2. Notify rider - they got paid for the return
        if order.delivery.rider_id:
            rider_token = await get_user_notification_token(
                db=db, user_id=order.delivery.rider_id
            )
            if rider_token:
                await send_push_notification(
                    tokens=[rider_token],
                    title="Return Confirmed",
                    message=(
                        f"The sender confirmed receipt of the returned package for order #{order.order_number}. "
                        "Your payment has been processed."
                    ),
                    navigate_to="/delivery/orders",
                )

        # 3. Notify dispatch company
        if order.delivery.dispatch_id:
            dispatch_token = await get_user_notification_token(
                db=db, user_id=order.delivery.dispatch_id
            )
            if dispatch_token:
                await send_push_notification(
                    tokens=[dispatch_token],
                    title="Return Confirmed",
                    message=(
                        f"Return confirmed for cancelled order #{order.order_number}. "
                        f"₦{order.delivery.amount_due_dispatch} has been credited to your wallet."
                    ),
                    navigate_to="/delivery/orders",
                )

        # 4. Notify sender - they were charged
        sender_token = await get_user_notification_token(
            db=db, user_id=order.owner_id
        )
        if sender_token:
            await send_push_notification(
                tokens=[sender_token],
                title="Return Confirmed",
                message=(
                    f"You confirmed receipt of the returned package for order #{order.order_number}. "
                    f"You have been charged ₦{order.delivery.delivery_fee} for the delivery service."
                ),
                navigate_to="/delivery/orders",
            )

        logger.info(f"Successfully sent return confirmation notifications for order {order.id}")

    except Exception as e:
        logger.error(
            f"Failed to send some return notifications for order {order.id}: {str(e)}",
            exc_info=True,
        )

async def _process_cancelled_order_return_settlement(order: Order):
    """
    Process settlement when sender confirms receipt of RETURNED package (cancelled order).
    
    In this case:
    - Sender is charged the full delivery_fee (it stays in escrow but moves to dispatch)
    - Dispatch receives amount_due_dispatch
    - This compensates the rider for the work of picking up and returning the package
    
    Args:
        order: Cancelled order instance with loaded delivery relationship

    Raises:
        HTTPException: If wallet operations fail after retries
    """
    # Idempotency key to prevent duplicate processing
    idempotency_key = f"cancelled_return_settlement:{order.id}:{order.delivery.id}"
    cache_key = f"idempotency:{idempotency_key}"
    
    # Check if already processed
    if redis_client.get(cache_key):
        logger.info(
            f"Cancelled order return settlement for order {order.id} already processed."
        )
        return

    MAX_RETRIES = 3
    retry_count = 0
    settlement_succeeded = False

    while retry_count < MAX_RETRIES and not settlement_succeeded:
        try:
            # Calculate amounts
            dispatch_amount = order.delivery.amount_due_dispatch
            total_charged = order.delivery.delivery_fee

            # Validate amounts
            if dispatch_amount < 0 or total_charged < 0:
                raise ValueError("Settlement amounts cannot be negative")
            if dispatch_amount > total_charged:
                raise ValueError("Dispatch amount cannot exceed total fee")

            logger.info(
                f"Processing cancelled order return settlement for order {order.id}: "
                f"Sender charged: {total_charged}, Dispatch receives: {dispatch_amount}"
            )

            # 1. Update dispatch company wallet - move from escrow to balance
            # The escrow was already allocated at pickup, now we just move it to balance
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.delivery.dispatch_id),
                    "balance_change": str(dispatch_amount),
                    "escrow_change": str(-total_charged),
                    "idempotency_key": idempotency_key,
                    "details": {
                        "order_id": str(order.id),
                        "operation": "cancelled_return_settlement",
                        "order_number": order.order_number,
                        "note": "Payment for returning cancelled package",
                    },
                },
            )

            # 2. Update sender wallet - clear escrow (sender is charged)
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.owner_id),
                    "balance_change": "0",
                    "escrow_change": str(-total_charged),
                    "idempotency_key": idempotency_key,
                    "details": {
                        "order_id": str(order.id),
                        "operation": "cancelled_return_charge",
                        "order_number": order.order_number,
                        "note": "Charged for cancelled delivery (package was picked up and returned)",
                    },
                },
            )

            # 3. Record settlement transaction for audit
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(order.delivery.dispatch_id),
                    "tx_ref": str(uuid.uuid4()),
                    "amount": str(dispatch_amount),
                    "transaction_type": TransactionType.USER_TO_USER,
                    "transaction_direction": TransactionDirection.CREDIT,
                    "payment_status": PaymentStatus.PAID,
                    "payment_method": PaymentMethod.ESCROW_SETTLEMENT,
                    "from_user": f"Order #{order.order_number} (Cancelled - Return)",
                    "idempotency_key": idempotency_key,
                    "details": {
                        "order_id": str(order.id),
                        "delivery_id": str(order.delivery.id),
                        "settlement_type": "cancelled_package_return",
                        "note": "Payment for returning cancelled package to sender",
                    },
                },
            )

            # Set idempotency marker (expires after 24 hours)
            redis_client.setex(cache_key, 86400, "1")
            
            settlement_succeeded = True
            logger.info(
                f"Cancelled order return settlement completed for order {order.id}: "
                f"dispatch_amount={dispatch_amount}, sender_charged={total_charged}"
            )

        except Exception as e:
            retry_count += 1
            logger.error(
                f"Cancelled return settlement attempt {retry_count} failed for order {order.id}: {str(e)}",
                exc_info=True,
            )
            if retry_count >= MAX_RETRIES:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to process return settlement after {MAX_RETRIES} attempts: {str(e)}",
                )



async def _order_settlement(order: Order):
    """
    Process wallet settlements for order completion with retries and failure handling.
    All wallet operations are atomic and must either all succeed or all fail.

    Args:
        order: Order instance

    Raises:
        HTTPException: If wallet operations fail after retries
    """
    # Idempotency key to prevent duplicate processing
    idempotency_key = f"order_settlement:{order.id}"
    cache_key = f"idempotency:{idempotency_key}"
    
    # Check if already processed
    if redis_client.get(cache_key):
        logger.info(f"Order settlement for order {order.id} already processed. Skipping.")
        return

    MAX_RETRIES = 3
    retry_count = 0
    settlement_succeeded = False

    while retry_count < MAX_RETRIES and not settlement_succeeded:
        try:
            # Calculate and validate amounts
            if order.amount_due_vendor <= 0 or order.grand_total <= 0:
                raise ValueError("Settlement amounts cannot be negative or zero")
            if order.amount_due_vendor > order.grand_total:
                raise ValueError("Vendor amount cannot exceed total")

            # 1. Update vendor wallet - move from escrow to balance
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.vendor_id),
                    "balance_change": str(order.amount_due_vendor),
                    "escrow_change": str(-abs(order.grand_total)),
                    "idempotency_key": idempotency_key,
                    "details": {
                        "order_id": str(order.id),
                        "operation": "order_settlement",
                        "order_number": order.order_number,
                    },
                },
            )

            # 2. Update customer wallet - clear escrow
            logger.info(
                f"Sending customer wallet update for order {order.id}: "
                f"wallet_id={order.owner_id}, escrow_change={-abs(order.grand_total)}"
            )
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.owner_id),
                    "balance_change": "0",
                    "escrow_change": str(-abs(order.grand_total)),
                    "idempotency_key": idempotency_key,
                    "details": {
                        "order_id": str(order.id),
                        "operation": "order_settlement",
                        "order_number": order.order_number,
                    },
                },
            )

            # 3. Record settlement transaction
            await producer.publish_message(
                service="wallet",
                operation="create_transaction",
                payload={
                    "wallet_id": str(order.vendor_id),
                    "tx_ref": str(uuid.uuid4()),
                    "to_wallet_id": str(order.vendor_id),
                    "amount": str(order.amount_due_vendor),
                    "transaction_type": TransactionType.USER_TO_USER,
                    "transaction_direction": TransactionDirection.CREDIT,
                    "payment_status": PaymentStatus.PAID,
                    "payment_method": PaymentMethod.ESCROW_SETTLEMENT,
                    "from_user": order.owner.profile.full_name if order.owner else order.owner.email,
                    "idempotency_key": idempotency_key,
                    "details": {
                        "order_id": str(order.id),
                        "settlement_type": order.order_type.value,
                        "commission": str(order.grand_total - order.amount_due_vendor),
                    },
                },
            )

            # Set idempotency marker (expires after 24 hours)
            redis_client.setex(cache_key, 86400, "1")
            
            settlement_succeeded = True
            logger.info(
                f"Order settlement completed for order {order.id}: "
                f"vendor_amount={order.amount_due_vendor}, total={order.grand_total}"
            )

        except Exception as e:
            retry_count += 1
            logger.error(
                f"Order settlement attempt {retry_count} failed for order {order.id}: {str(e)}",
                exc_info=True,
            )
            if retry_count >= MAX_RETRIES:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to process order settlement after {MAX_RETRIES} attempts",
                )



async def _clear_sender_escrow(order: Order, idempotency_key: str):
    cache_key = f"idempotency:{idempotency_key}"
    
    # Check if already processed
    if redis_client.get(cache_key):
        logger.info(f"Sender escrow clear already processed for order {order.id}. Skipping.")
        return

    total_spent = order.delivery.delivery_fee

    await producer.publish_message(
        service="wallet",
        operation="update_wallet",
        payload={
            "wallet_id": str(order.owner_id),
            "balance_change": "0",
            "escrow_change": str(-total_spent),
            "idempotency_key": idempotency_key,
            "details": {
                "order_id": str(order.id),
                "operation": "sender_escrow_clear",
                "order_number": order.order_number,
            },
        },
    )

    # Mark as processed
    redis_client.setex(cache_key, 86400, "1")  # 24 hours
    logger.info(f"Sender escrow cleared for order {order.id}")

async def _settle_dispatch(order: Order, idempotency_key: str):
    total_spent = order.delivery.delivery_fee
    dispatch_amount = order.delivery.amount_due_dispatch

    await producer.publish_message(
        service="wallet",
        operation="update_wallet",
        payload={
            "wallet_id": str(order.delivery.dispatch_id),
            "balance_change": str(dispatch_amount),
            "escrow_change": str(-total_spent),
            "idempotency_key": idempotency_key,
            "details": {
                "order_id": str(order.id),
                "operation": "dispatch_settlement",
                "order_number": order.order_number,
            },
        },
    )
    await producer.publish_message(
            service="wallet",
            operation="update_transaction",
            payload={
                "wallet_id": str(order.owner_id),
                "tx_ref": str(order.tx_ref),
                "to_user": order.owner.profile.business_name if order.owner.profile else order.owner.email,
            },
        )


    await producer.publish_message(
        service="wallet",
        operation="create_transaction",
        payload={
            "wallet_id": str(order.delivery.dispatch_id),
            "tx_ref": str(order.tx_ref),
            "to_wallet_id": str(order.delivery.dispatch_id),
            "amount": str(order.delivery.amount_due_dispatch),
            "transaction_type": transaction.transaction_type,
            "transaction_direction": TransactionDirection.CREDIT,
            "payment_method": transaction.payment_method,
            "payment_status": transaction.payment_status,
            "from_user": sender_profile.full_name if sender_profile.full_name else sender_profile.business_name,
            "to_user": dispatch_profile.full_name if dispatch_profile.full_name else dispatch_profile.business_name,
        },
    )
    redis_client.setex(f"idempotency:{idempotency_key}", 86400, "1")



async def _package_settlement(order: Order):
    """
    1. Move money from dispatch escrow → dispatch balance
    2. ALWAYS clear the sender’s escrow (idempotent on wallet side)
    """
    # ---- 1. Dispatch settlement (idempotent) ----
    dispatch_key = f"dispatch_settlement:{order.id}:{order.delivery.id}"
    if redis_client.get(f"idempotency:{dispatch_key}"):
        logger.info(f"Dispatch settlement already done for order {order.id}")
    else:
        await _settle_dispatch(order, dispatch_key)

    # ---- 2. Sender escrow clear (always run – wallet service is idempotent) ----
    sender_key = f"sender_escrow_clear:{order.id}"
    await _clear_sender_escrow(order, sender_key)


async def customer_confirm_order_received(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    Process customer confirmation of food/laundry order receipt.
    Handles order status update, wallet settlement, notifications and audit logging.

    Args:
        db: Database session
        order_id: UUID of order to confirm
        current_user: User confirming the order

    Returns:
        DeliveryStatusUpdateSchema with updated status

    Raises:
        HTTPException: With appropriate status code and message
    """
    try:
        # 1. Fetch order with required relationships
        result = await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.vendor).selectinload(User.profile),
                selectinload(Order.owner).selectinload(User.profile)
            )
            .with_for_update()
        )
        order = result.scalar_one_or_none()

        # 2. Validate order state and authorization
        await _validate_order_confirmation(order, current_user)

        # 3. Update order status
        try:
            await _update_order_status(order, db, OrderStatus.RECEIVED)
        except Exception as e:
            logger.error(
                f"Failed to update order status for {order.id}: {str(e)}", exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update order status",
            )

        # 4. Process wallet settlement
        try:
            await _order_settlement(order)
        except Exception as e:
            logger.error(
                f"Settlement failed for order {order.id}: {str(e)}", exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process payment settlement",
            )

        # 5. Create audit log
        try:
            await TransactionLogService.create_log(
                db=db,
                vendor_id=current_user.id,
                order_id=order.id,
                amount=order.grand_total - order.amount_due_vendor,
                action=TransactionLogAction.RECEIVED,
                status=order.order_payment_status,
                details={
                    "order_type": order.order_type.value,
                    "order_number": order.order_number,
                    "confirmed_by": current_user.profile.full_name if current_user.profile else
                     current_user.profile.business_name,
                    "phone_number": current_user.profile.phone_number,
                    "vendor": order.vendor.profile.business_name
                    or order.vendor.profile.full_name,
                    "amount_due_vendor": str(order.amount_due_vendor),
                    "total_amount": str(order.grand_total),
                    "commission": str(order.grand_total - order.amount_due_vendor),
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to create audit log for order {order.id}: {str(e)}",
                exc_info=True,
            )

        await db.refresh(order)

        # 6. Post-confirmation tasks (non-critical)
        try:
            # Send notifications
            await _notify_order_completion(order, db)

            # Invalidate caches
            _invalidate_order_caches(order, current_user)

        except Exception as e:
            logger.warning(
                f"Non-critical post-confirmation tasks failed for order {order.id}: {str(e)}",
                exc_info=True,
            )

        # 7. Log successful completion
        redis_client.delete(f"order_by_id:{order_id}")
        logger.info(f"Order confirmation completed successfully for order {order.id}")

        return DeliveryStatusUpdateSchema(order_status=order.order_status)

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to confirm order {order_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "An unexpected error occurred while confirming the order. "
                "Please try again or contact support if the issue persists."
            ),
        )


async def _validate_order_confirmation(order: Order, current_user: User):
    """
    Validates order state and user authorization for order confirmation.

    Args:
        order: Order instance to validate
        current_user: User attempting to confirm the order

    Raises:
        HTTPException: With appropriate status code and message
    """
    try:
        # Basic validation
        if not order:
            logger.error(f"Attempted to confirm non-existent order")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
            )

        # Authorization check
        if order.owner_id != current_user.id:
            logger.warning(
                f"Unauthorized confirmation attempt: User {current_user.id} "
                f"tried to confirm order {order.id} owned by {order.owner_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to confirm this order",
            )

        # Order type validation
        if order.order_type not in [OrderType.FOOD, OrderType.LAUNDRY]:
            logger.error(f"Invalid order type {order.order_type} for confirmation")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This confirmation method is only for food and laundry orders",
            )

        # Payment validation
        if order.order_payment_status != PaymentStatus.PAID:
            logger.warning(f"Attempted to confirm unpaid order {order.id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot confirm order - payment is not complete",
            )

        # Transaction reference check
        if not order.tx_ref:
            logger.error(f"Order {order.id} missing transaction reference")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This order cannot be confirmed - missing transaction reference",
            )

        # Status validation
        if order.order_status == OrderStatus.RECEIVED:
            logger.warning(f"Duplicate confirmation attempt for order {order.id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This order has already been confirmed as received",
            )

        if order.order_status != OrderStatus.DELIVERED:
            logger.warning(
                f"Invalid status transition attempt for order {order.id}: "
                f"from {order.order_status} to RECEIVED"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot confirm order - must be marked as delivered by vendor first",
            )

        # Amount validation
        if order.amount_due_vendor <= 0 or order.grand_total <= 0:
            logger.error(
                f"Invalid amounts for order {order.id}: "
                f"amount_due_vendor={order.amount_due_vendor}, total={order.grand_total}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid order amounts"
            )

        logger.info(f"Order validation successful for confirmation of order {order.id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error validating order confirmation: {str(e)}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while validating the order",
        )


async def _notify_order_completion(order: Order, db: AsyncSession):
    """
    Send comprehensive notifications about order completion to all relevant parties.

    Args:
        order: Order instance
        db: Database session
    """
    try:
        # 1. Broadcast WebSocket updates
        await ws_service.broadcast_order_status_update(
            order_id=str(order.id), new_status=order.order_status.value
        )

        # 2. Notify vendor
        vendor_token = await get_user_notification_token(db=db, user_id=order.vendor_id)
        if vendor_token:
            await send_push_notification(
                tokens=[vendor_token],
                title="Order Completed",
                message=(
                    f"Order #{order.order_number} has been confirmed by the customer. "
                    f"₦{order.amount_due_vendor} has been credited to your wallet."
                ),
                navigate_to="/delivery/orders",
            )

        # 3. Notify customer (confirmation receipt)
        customer_token = await get_user_notification_token(
            db=db, user_id=order.owner_id
        )
        if customer_token:
            business_name = (
                order.vendor.profile.business_name if order.vendor.profile else order.vendor.profile.full_name
            )
            await send_push_notification(
                tokens=[customer_token],
                title="Order Confirmation",
                message=(
                    f"You have confirmed receipt of your {order.order_type.value.lower()} "
                    f"order #{order.order_number} from {business_name}. Thank you for using our service!"
                ),
                navigate_to="/delivery/orders",
            )

        logger.info(f"Successfully sent completion notifications for order {order.id}")

    except Exception as e:
        logger.error(
            f"Failed to send some notifications for order {order.id}: {str(e)}",
            exc_info=True,
        )
       



def _invalidate_order_caches(order: Order, current_user: User):
     
    try:
        cache_keys = [
            ALL_DELIVERY,
            "paid_pending_deliveries", 
            "orders",
            "near_by_riders"
        ]
        redis_client.delete(f'wallet_transactions:{order.vendor_id}')
        redis_client.delete(f'wallet_transactions:{order.owner_id}')
        redis_client.delete(f"user_related_orders:{current_user.id}")
        redis_client.delete(f"user_related_orders:{order.vendor_id}")
        redis_client.delete(f"user_related_orders:{order.owner_id}")
        redis_client.delete(f"user_orders:{order.delivery.rider_id}")
        redis_client.delete(f"user_orders:{order.delivery.dispatch_id}")
        redis_client.delete(f"order_by_id:{order.id}")
        redis_client.delete(*cache_keys)
    except Exception as e:
        logger.warning(f"Failed to invalidate some order caches: {str(e)}")
    


async def sender_confirm_delivery_or_order_received(
    db: AsyncSession, order_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.delivery))
        .with_for_update()
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
    if order.require_delivery == RequireDeliverySchema.PICKED_UP:
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
        try:
            order.order_status = OrderStatus.RECEIVED
            await db.commit()
            await db.refresh(order)

            # Broadcast WebSocket update
            await ws_service.broadcast_order_status_update(
                order_id=order.id, new_status=order.order_status
            )

            # Notify vendor
            vendor_token = await get_user_notification_token(
                db=db, user_id=order.vendor_id
            )
            if vendor_token:
                await send_push_notification(
                    tokens=[vendor_token],
                    title="Order Completed",
                    message=f"Congratulations! Order completed. ₦{order.amount_due_vendor} has been credited to your wallet.",
                    navigate_to="/(app)/delivery/orders",
                )

            # Release funds from escrow
            # Update vendor wallet (move from escrow to balance)
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.vendor_id),
                    "balance_change": str(order.amount_due_vendor),
                    "escrow_change": str(-order.amount_due_vendor),
                },
            )

            # Update sender wallet (clear escrow)
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.owner_id),
                    "balance_change": str(0),
                    "escrow_change": str(-order.total_price),
                },
            )

            # Clear caches
            redis_client.delete(f"{ALL_DELIVERY}")
            redis_client.delete("paid_pending_deliveries")
            redis_client.delete(f"user_related_orders:{current_user.id}")
            redis_client.delete(f"user_related_orders:{order.vendor_id}")

            return DeliveryStatusUpdateSchema(order_status=order.order_status)

        except Exception as e:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Handle  laundry pickup
    if order.require_delivery == RequireDeliverySchema.VENDOR_PICKUP_AND_DROPOFF:
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
        try:
            order.order_status = OrderStatus.RECEIVED
            await db.commit()
            await db.refresh(order)

            # Broadcast WebSocket update
            await ws_service.broadcast_order_status_update(
                order_id=order.id, new_status=order.order_status
            )

            # Notify vendor
            vendor_token = await get_user_notification_token(
                db=db, user_id=order.vendor_id
            )
            if vendor_token:
                await send_push_notification(
                    tokens=[vendor_token],
                    title="Order Completed",
                    message=f"Order completed. Your wallet has been credited with ₦{order.amount_due_vendor}.",
                    navigate_to="/(app)/delivery/orders",
                )

            # Release funds from escrow
            # Update vendor wallet (move from escrow to balance)
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.vendor_id),
                    "balance_change": str(order.amount_due_vendor),
                    "escrow_change": str(-order.amount_due_vendor),
                },
            )

            # Update sender wallet (clear escrow)
            await producer.publish_message(
                service="wallet",
                operation="update_wallet",
                payload={
                    "wallet_id": str(order.owner_id),
                    "balance_change": str(0),
                    "escrow_change": str(-order.total_price),
                },
            )

            # Clear caches
            redis_client.delete(f"{ALL_DELIVERY}")
            redis_client.delete("paid_pending_deliveries")
            redis_client.delete(f"user_related_orders:{current_user.id}")
            redis_client.delete(f"user_related_orders:{order.vendor_id}")

            return DeliveryStatusUpdateSchema(order_status=order.order_status)

        except Exception as e:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

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

    total_spent = order.grand_total

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
                message=f"Congratulations! Your wallet has been updated.",
                navigate_to="/(app)/delivery/orders",
            )

        if vendor_token:
            await send_push_notification(
                tokens=[vendor_token],
                title="Order Completed",
                message=f"Congratulations! Your wallet has been updated",
                navigate_to="/(app)/delivery/orders",
            )

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

        redis_client.delete(f"{ALL_DELIVERY}")
        redis_client.delete("paid_pending_deliveries")
        redis_client.delete(f"user_related_orders:{current_user.id}")
        redis_client.delete(f"user_related_orders:{order.vendor_id}")
        redis_client.delete(f"user_related_orders:{order.delivery.dispatch_id}")
        redis_client.delete(f"user_related_orders:{order.delivery.rider_id}")

        return DeliveryStatusUpdateSchema(
            delivery_status=order.delivery.delivery_status,
            order_status=order.order_status,
        )

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def rider_mark_package_delivered(
    delivery_id: UUID, current_user: User, db: AsyncSession
) -> DeliveryStatusUpdateSchema:
    """
    Mark a package as delivered by the rider with comprehensive validation and error handling.

    Args:
        delivery_id: UUID of the delivery
        current_user: Currently authenticated user
        db: Database session

    Returns:
        DeliveryStatusUpdateSchema with updated status

    Raises:
        HTTPException: With appropriate status code and message for various failure cases
    """
    try:
        # Fetch delivery with order using pessimistic lock
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

        # Comprehensive validation
        if not delivery:
            logger.error(
                f"Delivery {delivery_id} not found for rider {current_user.id}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Delivery not found or has been deleted.",
            )

        # Authorization validation
        if (
            current_user.user_type != UserType.RIDER
            and delivery.rider_id != current_user.id
        ):
            logger.warning(
                f"Unauthorized delivery status update attempt by user {current_user.id}:{current_user.email} "
                f"for delivery {delivery_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to update this delivery's status.",
            )

        # Status transition validation
        if delivery.delivery_status not in [
            DeliveryStatus.ACCEPTED,
            DeliveryStatus.PICKED_UP,
        ]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot mark as delivered. Current status: {delivery.delivery_status.value}",
            )

        # Payment validation
        if delivery.order.order_payment_status != PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot mark as delivered: Order payment is not completed.",
            )

        # Idempotency check: If already delivered, return current status
        if delivery.delivery_status == DeliveryStatus.DELIVERED:
            logger.info(
                f"Delivery {delivery_id} already marked as delivered. Returning current status."
            )
            return DeliveryStatusUpdateSchema(
                delivery_status=delivery.delivery_status,
                order_status=delivery.order.order_status,
            )

        # Update delivery status
        delivery.delivery_status = DeliveryStatus.DELIVERED
        delivery.order.order_status = OrderStatus.DELIVERED

        await db.commit()

        # Send notifications to all stakeholders (after commit)
        try:
            await _notify_delivery_completion(delivery, db)
        except Exception as e:
            logger.error(
                f"Failed to send notifications for delivery {delivery_id}: {str(e)}",
                exc_info=True,
            )

        # Invalidate caches
        try:
            _invalidate_delivery_caches(delivery, current_user)
            _invalidate_pickup_order_caches(delivery.order, current_user)
        except Exception as e:
            logger.error(
                f"Failed to invalidate caches for delivery {delivery_id}: {str(e)}",
                exc_info=True,
            )

        logger.info(
            f"Successfully marked delivery {delivery_id} as delivered by rider {current_user.id}"
        )

        redis_client.delete(f"order_by_id:{delivery.order.id}")

        return DeliveryStatusUpdateSchema(
            delivery_status=delivery.delivery_status,
            order_status=delivery.order.order_status,
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(
            f"Unexpected error marking delivery {delivery_id} as delivered: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating delivery status.",
        )


async def _notify_delivery_completion(delivery: Delivery, db: AsyncSession):
    """
    Send notifications to all stakeholders about delivery completion.
    Implements retry logic for notification failures.
    """
    MAX_RETRIES = 3
    stakeholders = {
        "sender": delivery.sender_id,
        "dispatch": delivery.dispatch_id,
        "rider": delivery.rider_id,
    }

    for role, user_id in stakeholders.items():
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                token = await get_user_notification_token(db=db, user_id=user_id)
                if token:
                    message = {
                        "sender": "Your order has been delivered. Please confirm with the recipient before marking as received.",
                        "dispatch": "Rider has completed the delivery.",
                        "rider": "Delivery marked as completed successfully.",
                    }

                    await send_push_notification(
                        tokens=[token],
                        title="Delivery Update",
                        message=message[role],
                        navigate_to="/(app)/delivery",
                    )
                break
            except Exception as e:
                retry_count += 1
                if retry_count == MAX_RETRIES:
                    logger.error(
                        f"Failed to send notification to {role} after {MAX_RETRIES} attempts: {str(e)}",
                        exc_info=True,
                    )
                await asyncio.sleep(1)  # Wait before retry


async def _invalidate_delivery_caches(delivery: Delivery, current_user: User):
    """
    Invalidate all relevant caches related to a delivery.
    Handles each cache operation separately to prevent total failure.
    """
    cache_keys = [
        f"delivery:{delivery.id}",
        ALL_DELIVERY,
        "paid_pending_deliveries",
        f"user_related_orders:{current_user.id}",
        f"user_related_orders:{delivery.sender_id}",
        f"user_related_orders:{delivery.dispatch_id}",
        f"user_related_orders:{delivery.rider_id}",
        f"order_by_id: {delivery.order_id}",
    ]

    for key in cache_keys:
        try:
            redis_client.delete(key)
        except Exception as e:
            logger.error(f"Failed to invalidate cache key {key}: {str(e)}")
            continue  # Continue with other cache invalidations

    await ws_service.broadcast_delivery_status_update(
        delivery_id=delivery.id, new_status=delivery.delivery_status
    )
    await ws_service.broadcast_delivery_status_update(
        delivery_id=delivery.id, new_status=delivery.order.order_status
    )


async def update_delivery_order_location(
    db: AsyncSession, delivery_id: UUID, location_data: LocationData
) -> LocationData:
    # Update the delivery record
    stmt = select(Delivery).where(
        Delivery.id == delivery_id, Delivery.rider_id == location_data.rider_id
    )
    result = await db.execute(stmt)
    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery order not found or invalid rider.",
        )
    if delivery.delivery_status == DeliveryStatus.DELIVERED:
        logger.info(f"Delivery already comleted for {delivery_id}")
        return {
            "message": "Delivery already comleted.",
            "rider_id": delivery.rider_id,
            "last_known_rider_coordinates": delivery.last_known_rider_coordinates,
        }

    # Update coordinates
    delivery.last_known_rider_coordinates = location_data.last_known_rider_coordinates
    await db.commit()
    await db.refresh(delivery)

    message = {
        "type": "rider_location_update",
        "delivery_id": str(delivery.id),
        "coordinates": delivery.last_known_rider_coordinates,
        "timestamp": datetime.now().isoformat(),
    }

    data = {
        "rider_id": delivery.rider_id,
        "last_known_rider_coordinates": delivery.last_known_rider_coordinates,
    }

    # Send to customer
    if delivery.sender_id:
        await manager.send_personal_message(message, str(delivery.sender_id))

    redis_client.delete(f"user_orders:{delivery.sender_id}")
    redis_client.delete(f"user_orders:{delivery.rider_id}")

    return data


# <<<--- Admin Order Status Modification --->>>
async def admin_modify_order_status(
    db: AsyncSession,
    order_id: UUID,
    new_status: OrderStatus,  # Fixed: Changed from Order to OrderStatus
    current_user: User,
) -> DeliveryStatusUpdateSchema:
    """
    Allows an ADMIN user to forcibly change the status of any order and its related delivery.

    This function:
    1. Validates admin authorization and status transitions
    2. Updates order and delivery status atomically
    3. Processes wallet settlements with retries
    4. Creates audit logs
    5. Manages cache invalidation
    6. Sends notifications

    Args:
        db: Async database session
        order_id: The UUID of the order to modify
        new_status: The new OrderStatus enum value to set
        current_user: The admin user performing the action (must be ADMIN)

    Returns:
        DeliveryStatusUpdateSchema with updated statuses

    Raises:
        HTTPException:
            - 403: If user is not ADMIN
            - 404: If order not found
            - 400: If invalid status transition
            - 500: For system errors
    """
    # Admin authorization check
    if not current_user.is_admin:  # Fixed: Using proper attribute
        logger.warning(
            f"Non-admin user {current_user.id} attempted to modify status for order {order_id}",
            extra={
                "user_id": str(current_user.id),
                "user_type": str(current_user.user_type),
                "action": "admin_modify_order_status",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied. Only ADMIN users can modify order status directly.",
        )

    try:
        # Fetch order with related data using pessimistic lock
        result = await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.delivery),
                selectinload(Order.vendor).selectinload(User.profile),
                selectinload(Order.owner).selectinload(User.profile),
            )
            .with_for_update()
        )
        order = result.scalar_one_or_none()

        if not order:
            logger.error(f"Order {order_id} not found during admin status modification")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order {order_id} not found.",
            )

        # Validate status transition
        if order.order_status == new_status:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Order is already in {new_status.value} status",
            )

        # Payment validation for certain transitions
        if (
            new_status in [OrderStatus.COMPLETED, OrderStatus.RECEIVED]
            and order.order_payment_status != PaymentStatus.PAID
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change status: Order payment is not completed",
            )

        # Get profile
        dispatch_profile = await get_user_profile(order.delivery.dispatch_id, db=db)
        sender_profile = await get_user_profile(order.owner_id, db=db)

        transaction_result = await db.execute(
            select(Transaction).where(Transaction.tx_ref == order.tx_ref)
        )
        transaction = transaction_result.scalar_one_or_none()

        old_status = order.order_status
        # Get transaction details if needed for wallet operations
        if new_status in [OrderStatus.COMPLETED, OrderStatus.RECEIVED]:
            transaction_result = await db.execute(
                select(Transaction).where(Transaction.tx_ref == order.tx_ref)
            )
            transaction = transaction_result.scalar_one_or_none()

            if not transaction:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Transaction record not found for this order",
                )

        # Store original status for audit
        old_status = order.order_status

        # Update order status
        order.order_status = new_status
        if order.delivery:
            order.delivery.delivery_status = (
                DeliveryStatus.RECEIVED
                if new_status == OrderStatus.RECEIVED
                else order.delivery.delivery_status
            )

        # Process wallet operations if completing/receiving the order
        if new_status in [OrderStatus.COMPLETED, OrderStatus.RECEIVED]:
            MAX_RETRIES = 3
            retry_count = 0
            settlement_succeeded = False

            while retry_count < MAX_RETRIES and not settlement_succeeded:
                try:
                    # Update vendor wallet
                    await producer.publish_message(
                        service="wallet",
                        operation="update_wallet",
                        payload={
                            "wallet_id": str(order.vendor_id),
                            "tx_ref": str(order.tx_ref),
                            "balance_change": str(order.amount_due_vendor),
                            "escrow_change": str(-order.amount_due_vendor),
                        },
                    )

                    # Update dispatch wallet if applicable
                    if order.delivery and order.delivery.amount_due_dispatch > 0:
                        await producer.publish_message(
                            service="wallet",
                            operation="update_wallet",
                            payload={
                                "wallet_id": str(order.delivery.dispatch_id),
                                "balance_change": str(
                                    order.delivery.amount_due_dispatch
                                ),
                                "escrow_change": str(
                                    -order.delivery.amount_due_dispatch
                                ),
                            },
                        )

                    # Update owner wallet
                    await producer.publish_message(
                        service="wallet",
                        operation="update_wallet",
                        payload={
                            "wallet_id": str(order.owner_id),
                            "balance_change": "0",
                            "escrow_change": str(-order.amount_due_vendor),
                        },
                    )

                    settlement_succeeded = True
                    logger.info(
                        f"Successfully processed wallet settlements for order {order.id}"
                    )

                except Exception as e:
                    retry_count += 1
                    if retry_count == MAX_RETRIES:
                        logger.error(
                            f"Failed to process wallet settlements after {MAX_RETRIES} retries: {str(e)}",
                            exc_info=True,
                        )
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to process wallet settlements. Please try again.",
                        )
                    await asyncio.sleep(1)  # Wait before retrying

        # --- AUDIT LOG ---
        audit = AuditLog(
            actor_id=current_user.get("id"),
            actor_name=current_user.get("email", "unknown"),
            actor_role=str(current_user.get("user_type", "unknown")),
            action="admin_modify_order_status",
            resource_type="Order",
            resource_id=order_id,
            resource_summary=f"Admin changed order status for {order_id}",
            changes={"order_status": [str(old_status), str(new_status)]},
            extra_metadata=None,
        )
        db.add(audit)
        await db.commit()

        # Centralized cache invalidation with error handling
        cache_keys = [
            f"order:{order.id}",
            f"delivery:{order.delivery.id if order.delivery else ''}",
            f"user_orders:{order.owner_id}",
            f"user_orders:{order.vendor_id}",
            "all_orders",
            "all_deliveries",
            ALL_DELIVERY,
            "paid_pending_deliveries",
        ]

        if order.delivery:
            cache_keys.extend(
                [
                    f"user_orders:{order.delivery.dispatch_id}",
                    f"user_orders:{order.delivery.rider_id}",
                ]
            )

        # Batch delete with error handling
        for key in cache_keys:
            try:
                if key:  # Skip empty keys
                    redis_client.delete(key)
            except Exception as e:
                logger.warning(
                    f"Failed to invalidate cache key {key}: {str(e)}", exc_info=True
                )
                continue  # Continue with remaining cache keys

        await ws_service.broadcast_order_status_update(
            order_id=order.id, new_status=order.order_status
        )

        if order.delivery:
            # Queue order and delivery status update
            await producer.publish_message(
                service="order_status",
                operation="update_order_status",
                payload={
                    "order_id": str(order.id),
                    "delivery_id": str(order.delivery.id),
                    "order_status": OrderStatus.RECEIVED,
                    "delivery_status": DeliveryStatus.RECEIVED,
                },
            )

        await producer.publish_message(
            service="wallet",
            operation="create_transaction",
            payload={
                "wallet_id": str(order.delivery.dispatch_id),
                "tx_ref": str(order.tx_ref),
                "to_wallet_id": str(order.delivery.dispatch_id),
                "amount": str(order.delivery.amount_due_dispatch),
                "transaction_type": transaction.transaction_type,
                "transaction_direction": TransactionDirection.CREDIT,
                "payment_method": transaction.payment_method,
                "payment_status": transaction.payment_status,
                "from_user": sender_profile.full_name or sender_profile.business_name,
                "to_user": dispatch_profile.full_name or dispatch_profile.business_name,
            },
        )

        # Update Vendor wallet (move from escrow to balance)
        await producer.publish_message(
            service="wallet",
            operation="update_wallet",
            payload={
                "wallet_id": str(order.vendor_id),
                "balance_change": str(order.amount_due_vendor),
                "escrow_change": str(-order.amount_due_vendor),
            },
        )

        await producer.publish_message(
            service="wallet",
            operation="create_transaction",
            payload={
                "wallet_id": str(order.vendor_id),
                "tx_ref": str(order.tx_ref),
                "to_wallet_id": str(order.vendor_id),
                "amount": str(order.amount_due_vendor),
                "transaction_type": transaction.transaction_type,
                "transaction_direction": TransactionDirection.CREDIT,
                "payment_method": transaction.payment_method,
                "payment_status": transaction.payment_status,
                "from_user": sender_profile.full_name or sender_profile.business_name,
                "to_user": dispatch_profile.full_name or dispatch_profile.business_name,
            },
        )
        # Update owner wallet (remove escrow)
        await producer.publish_message(
            service="wallet",
            operation="update_wallet",
            payload={
                "wallet_id": str(order.owner_id),
                "balance_change": "0",
                "escrow_change": str(-order.amount_due_vendor),
            },
        )

        await producer.publish_message(
            service="wallet",
            operation="create_transaction",
            payload={
                "wallet_id": str(order.owner_id),
                "tx_ref": str(order.tx_ref),
                "to_wallet_id": str(order.owner_id),
                "amount": str(order.grand_total),
                "transaction_type": transaction.transaction_type,
                "transaction_direction": TransactionDirection.DEBIT,
                "payment_method": transaction.payment_method,
                "payment_status": transaction.payment_status,
                "from_user": sender_profile.full_name or sender_profile.business_name,
                "to_user": dispatch_profile.full_name or dispatch_profile.business_name,
            },
        )

        return DeliveryStatusUpdateSchema(delivery_status=new_status)

    except HTTPException:
        await db.rollback()
        logger.error(
            "Admin order status modification failed with HTTP exception",
            exc_info=True,
            extra={
                "order_id": str(order_id),
                "admin_id": str(current_user.id),
                "attempted_status": str(new_status),
            },
        )
        raise
    except Exception as e:
        await db.rollback()
        logger.error(
            f"Unexpected error in admin order status modification: {str(e)}",
            exc_info=True,
            extra={
                "order_id": str(order_id),
                "admin_id": str(current_user.id),
                "attempted_status": str(new_status),
                "error_type": type(e).__name__,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while modifying order status. Please try again or contact support.",
        )


# <<< ----- UTILITY FUNCTIONS FOR ORDERS/DELIVERY ----- >>>


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
    db: AsyncSession,
    total_price: Decimal,
    pickup_dropoff_fee: Decimal = Decimal("0.00"),
) -> Decimal:
    """
    Calculate the amount due to vendor after platform commission.

    Args:
        db: Database session
        total_price: Total price of items
        pickup_dropoff_fee: Vendor's pickup/dropoff charge (if applicable)

    Returns:
        Amount due to vendor after commission
    """
    # Add pickup/dropoff fee to total
    total_with_fees = total_price + pickup_dropoff_fee

    # Get platform commission
    charge = await get_charges(db)

    # Calculate vendor's amount after commission
    commission_amount = total_with_fees * charge.food_laundry_commission_percentage
    amount_due_vendor = total_with_fees - commission_amount

    return amount_due_vendor


async def calculate_amount_due_vendor_old(
    db: AsyncSession,
    order_items: list[OrderItemCreate],
    pickup_dropoff_fee: Decimal = Decimal("0.00"),
) -> Decimal:
    total_price = Decimal("0.00")
    for item in order_items:
        # fetch each item price (meal or linen service)
        result = await db.execute(select(Item).where(Item.id == item.item_id))
        item_data = result.scalar_one_or_none()

        if not item_data:
            raise Exception("Invalid item selected")

        total_price += Decimal(item_data.price) * item.quantity

    total_price += pickup_dropoff_fee

    # 3. Calculate commission
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


def format_delivery_response(
    order: Order, distance: Optional[float] = None, delivery: Optional[Delivery] = None
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
            "last_known_rider_coordinates": delivery.last_known_rider_coordinates or None,
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
        "vendor_pickup_dropoff_charge": order.vendor_pickup_dropoff_charge,
        "order_number": order.order_number,
        "vendor_id": str(order.vendor_id),
        "business_name": order.vendor.profile.business_name
        or order.vendor.profile.full_name,
        "order_type": order.order_type.value,
        "require_delivery": order.require_delivery,
        "total_price": str(order.total_price),
        "order_payment_status": order.order_payment_status.value,
        "order_status": order.order_status,
        "amount_due_vendor": str(order.amount_due_vendor),
        "payment_link": order.payment_link or "",
        "order_items": order_items,
        "created_at": order.created_at.isoformat(),
        "cancel_reason": getattr(order, "cancel_reason", None),
    }

    return DeliveryResponse(order=order_data, delivery=delivery_data, distance=distance)


async def get_user_profile(user_id: UUID, db: AsyncSession):
    stmt = (
        select(Profile)
        .where(Profile.user_id == user_id)
        .options(selectinload(Profile.user))
    )
    result = await db.execute(stmt)

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
        CACHE_TTL,
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


async def get_paid_pending_deliveries(
    db: AsyncSession, current_user: User
) -> list[DeliveryResponse]:
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
                Order.order_type == "package",
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

    delivery_responses = []

    for order in orders:
        try:
            distance_km = await get_distance_between_addresses(
                vendor_address=order.delivery.origin, current_user=current_user
            )

            # Only include orders within 35km radius
            if distance_km is not None and distance_km <= 35.0:
                delivery_response = format_delivery_response(
                    order=order, delivery=order.delivery, distance=distance_km
                )
                delivery_responses.append(delivery_response)

        except Exception as e:
            print(f"Error calculating distance for order {order.id}: {e}")
            continue

    # Cache the results
    if delivery_responses:
        redis_client.setex(
            cache_key,
            settings.REDIS_EX,
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
        .outerjoin(Order.delivery)
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
                Delivery.dispatch_id == user_id,
                Delivery.rider_id == user_id,
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
        format_delivery_response(order=order, delivery=order.delivery)
        for order in orders
    ]

    redis_client.setex(
        cache_key,
        settings.REDIS_EX,
        json.dumps([d.model_dump() for d in delivery_responses], default=str),
    )

    return delivery_responses





async def cancel_order_old(
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
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.delivery))
        .with_for_update()
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
        logger.error(f"Error cancelling order {order_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel order",
        )
