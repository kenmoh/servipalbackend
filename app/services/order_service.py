from datetime import timedelta, datetime
import profile
from typing import Optional
from sqlalchemy import func, or_, and_, select, update, insert

# from sqlalchemy.sql.expression.ColumnOperators import in_
from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload
from app.models.models import (
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

import json
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.schemas import ReviewSchema
from app.schemas.status_schema import OrderStatus, TransactionType


from app.schemas.order_schema import (
    PaymentStatus,
    OrderItemResponseSchema,
    OrderItemCreate,
    PackageCreate,
    DeliveryStatusUpdateSchema,
    OrderAndDeliverySchema,
)
from app.schemas.delivery_schemas import (
    DeliveryResponse,
    DeliveryType,
)
from app.schemas.item_schemas import ItemType


from app.schemas.status_schema import RequireDeliverySchema, DeliveryStatus
from app.schemas.user_schemas import UserType, WalletRespose
from app.services.auth_service import invalidate_rider_cache
from app.utils.utils import (
    get_dispatch_id,
    get_payment_link,
    send_push_notification,
)
from app.config.config import redis_client
from app.utils.s3_service import add_image


async def get_user_notification_token(db: AsyncSession, user_id):
    result = await db.execute(select(User.notification_token).where(User.id == user_id))
    token = result.scalar_one()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification token missing"
        )
    return token


ALL_DELIVERY = "ALL_DELIVERY"


async def filter_delivery_by_delivery_type(
    delivery_type: DeliveryType, db: AsyncSession, skip: int = 0, limit: int = 20
) -> list[DeliveryResponse]:
    """
    Get all deliveries by delivery type with pagination and caching
    """
    cache_key = f"all_delivery_by_type"

    # Try cache first
    cached_deliveries = redis_client.get(cache_key)
    if cached_deliveries:
        return [DeliveryResponse(**d) for d in json.loads(cached_deliveries)]

    stmt = (
        select(Delivery)
        .where(Delivery.delivery_type == delivery_type)
        .options(
            joinedload(Delivery.order).options(
                selectinload(Order.order_items).options(
                    joinedload(OrderItem.item).options(
                        selectinload(Item.images))
                ),
                joinedload(Order.delivery),
            )
        )
        .offset(skip)
        .limit(limit)
        .order_by(Delivery.created_at.desc())
    )

    result = await db.execute(stmt)
    deliveries = result.unique().scalars().all()

    # Format responses
    delivery_responses = [
        format_delivery_response(delivery.order, delivery) for delivery in deliveries
    ]

    # Cache the formatted responses
    redis_client.setex(
        cache_key,
        timedelta(seconds=CACHE_TTL),
        json.dumps([d.model_dump() for d in delivery_responses], default=str),
    )

    return delivery_responses


async def get_delivery_by_id(db: AsyncSession, delivery_id: UUID) -> DeliveryResponse:
    """
    Properly loads all relationships including item images without errors
    """

    cached_delivery = redis_client.get(f"delivery:{delivery_id}")
    if cached_delivery:
        return DeliveryResponse(**json.loads(cached_delivery))

    stmt = (
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .options(
            joinedload(Delivery.order).options(
                selectinload(Order.order_items).options(
                    joinedload(OrderItem.item).options(
                        selectinload(Item.images)  # This loads the item images
                    )
                ),
                joinedload(Order.delivery),  # This loads the order's delivery
            )
        )
    )

    result = await db.execute(stmt)
    delivery = result.unique().scalars().one_or_none()

    if not delivery:
        return None
    delivery_response = format_delivery_response(delivery.order, delivery)

    # Cache the formatted response
    redis_client.setex(
        f"delivery:{delivery_id}",
        timedelta(seconds=CACHE_TTL),
        json.dumps(delivery_response.model_dump(), default=str),
    )

    return delivery_response


async def get_all_deliveries(
    db: AsyncSession, skip: int = 0, limit: int = 20
) -> list[DeliveryResponse]:
    """
    Get all deliveries with pagination and caching
    """
    cache_key = ALL_DELIVERY

    # Try cache first
    cached_deliveries = redis_client.get(cache_key)
    if cached_deliveries:
        return [DeliveryResponse(**d) for d in json.loads(cached_deliveries)]

    stmt = (
        select(Delivery)
        .options(
            joinedload(Delivery.order).options(
                selectinload(Order.order_items).options(
                    joinedload(OrderItem.item).options(
                        selectinload(Item.images))
                ),
                joinedload(Order.delivery),
            )
        )
        .offset(skip)
        .limit(limit)
        .order_by(Delivery.created_at.desc())
    )

    result = await db.execute(stmt)
    deliveries = result.unique().scalars().all()

    # Format responses
    delivery_responses = [
        format_delivery_response(delivery.order, delivery) for delivery in deliveries
    ]

    # Cache the formatted responses
    redis_client.setex(
        cache_key,
        timedelta(seconds=CACHE_TTL),
        json.dumps([d.model_dump() for d in delivery_responses], default=str),
    )

    return delivery_responses


async def create_package_order(
    db: AsyncSession, data: PackageCreate, image: UploadFile, current_user: User
) -> DeliveryResponse:
    if current_user.user_type == UserType.CUSTOMER and not (
        current_user.profile.full_name or current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and full name are required. Please update your profile!",
        )
    if current_user.user_type == UserType.VENDOR and not (
        current_user.profile.business_name or current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and business name are required. Please update your profile!",
        )
    if current_user.user_type in [UserType.RIDER, UserType.DISPATCH] and not (
        current_user.profile.full_name or current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and full name are required. Please update your profile!",
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
            .returning(Order.id, Order.id, Order.owner_id)
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
            id=delivery_data.order_id,
            amount=delivery_data.delivery_fee,
            current_user=current_user,
        )

        # --- 6. Update Order with payment link and total price ---

        await db.execute(
            update(Order)
            .where(Order.id == delivery_data.order_id)
            .values({"payment_link": payment_link, "total_price": total_amount_due})
        )

        await db.commit()

        invalidate_order_cache(delivery_data.order_id)
        redis_client.delete(f"user_orders:{current_user.id}")
        redis_client.delete(f"{ALL_DELIVERY}")
        if hasattr(delivery_data, "vendor_id"):
            redis_client.delete(f"vendor_orders:{delivery_data.vendor_id}")
        else:
            redis_client.delete(f"vendor_orders:{current_user.id}")

        stmt = (
            select(Order)
            .where(Order.id == delivery_data.order_id)
            .options(
                selectinload(Order.order_items).options(
                    joinedload(OrderItem.item).options(
                        selectinload(Item.images))
                )
            )
        )
        order = (await db.execute(stmt)).scalar_one()

        delivery_stmt = select(Delivery).where(Delivery.id == delivery_data.id)
        delivery = (await db.execute(delivery_stmt)).scalar_one()

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
    if current_user.user_type == UserType.VENDOR and not (
        current_user.profile.business_name and current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and business name are required. Please update your profile!",
        )

    # Batch fetch all items at once - filter by vendor_id for additional validation
    item_ids = [
        UUID(item.item_id) if isinstance(item.item_id, str) else item.item_id
        for item in order_item.order_items
    ]
    items_result = await db.execute(
        select(Item).where(Item.id.in_(item_ids)).where(
            Item.user_id == vendor_id)
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
        total_price += Decimal(item_data.price) * \
            Decimal(order_item_detail.quantity)
        item_types.add(item_data.item_type)

    # Validate single item type
    if len(item_types) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All items in the order must be of the same type (either all meals or all laundry items)",
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
            .returning(Order.id)
        )

        order_id = order_insert_result.scalar_one()

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
            delivery_insert_result = await db.execute(
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
            delivery_id = delivery_insert_result.scalar_one()

        # Generate payment link
        payment_link = await get_payment_link(order_id, final_amount, current_user)

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
        ]
        redis_client.delete(*cache_keys)

        # Single optimized query to fetch complete order and delivery data
        if requires_delivery:
            stmt = (
                select(Order, Delivery)
                .join(Delivery, Order.id == Delivery.order_id)
                .where(Order.id == order_id)
                .options(
                    selectinload(Order.order_items).options(
                        joinedload(OrderItem.item).options(
                            selectinload(Item.images))
                    )
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
                        joinedload(OrderItem.item).options(
                            selectinload(Item.images))
                    )
                )
            )
            order = (await db.execute(stmt)).scalar_one()

            redis_client.delete(f"{ALL_DELIVERY}")

            token = get_user_notification_token(db=db, user_id=vendor_id)
            await send_push_notification(
                tokens=[token],
                title="New Order",
                message=f"You have a new order from {current_user.profile.full_name if current_user.profile.full_name else current_user.profile.business_name}",
                navigate_to="/delivery/orders",
            )
            return format_delivery_response(order, delivery=None)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create order - {e}",
        )


async def get_order_with_items(
    db: AsyncSession, order_id: UUID
) -> OrderItemResponseSchema:
    """
    Fetches an order and its associated items based on the order ID.
    Args:
        supabase: Supabase client instance.
        order_id: The ID of the order to fetch.
    Returns:
        A dictionary containing the order details and associated items.
    Raises:
        Exception: If the order is not found.
    """
    #  # Try cache first
    cached_order = await get_cached_order(order_id)
    if cached_order:
        return OrderItemResponseSchema(**cached_order)

    # If not in cache, fetch from database
    stmt = (
        select(Order)
        .options(joinedload(Order.items), joinedload(Order.delivery))
        .where(Order.id == order_id)
    )

    result = await db.execute(stmt)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    # Format response
    order_data = {
        "id": order.id,
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "price": str(item.price),
                "quantity": item.quantity,
                "url": item.url,
            }
            for item in order.items
        ],
        "total_price": str(order.total_price),
        "status": order.status,
        "created_at": order.created_at,
    }

    # Cache the formatted response
    set_cached_order(order_id, order_data)

    return OrderItemResponseSchema(**order_data)


async def cancel_delivery(
    db: AsyncSession, delivery_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    cancel delivery(Owner/Rider/Dispatch)
    """

    if current_user.user_type in [UserType.CUSTOMER, UserType.VENDOR]:
        result = await db.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .where(Delivery.sender_id == current_user.id)
        )
        delivery = result.scalar_one_or_none()

        if delivery.delivery_status not in [
            DeliveryStatus.IN_TRANSIT,
            DeliveryStatus.DELIVERED,
            DeliveryStatus.RECEIVED,
            DeliveryStatus.LAUNDRY_DELIVERES_TO_VENDORR,
        ]:
            result = await db.execute(
                update(Delivery)
                .where(Delivery.id == delivery_id)
                .where(Delivery.sender_id == current_user.id)
                .values(delivery_status=DeliveryStatus.CANCELLED)
            )
            await db.commit()
            # UPDATE USER WALLET HERE
            delivery_result = result.scalar_one_or_none()
            invalidate_delivery_cache(delivery_result.id)
            return

    if current_user.user_type in [UserType.RIDER, UserType.DISPATCH]:
        result = await db.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .where(
                or_(
                    Delivery.rider_id == current_user.id,
                    Delivery.dispatch_id == current_user.id,
                )
            )
        )

        delivery = result.scalar_one_or_none()

        if delivery.delivery_status == DeliveryStatus.IN_TRANSIT:
            result = await db.execute(
                update(Delivery)
                .values(
                    delivery_status=DeliveryStatus.PENDING,
                    rider_id=None,
                    dispatch_id=None,
                    rider_phone_number=None,
                )
                .where(Delivery.id == delivery_id)
                .where(
                    or_(
                        Delivery.rider_id == current_user.id,
                        Delivery.dispatch_id == current_user.id,
                    )
                )
            )

            current_user.order_cancel_count += 1

            # UPDATE RIDER ESCROW BALANCE HERE
            await db.commit()

            delivery_result = result.scalar_one_or_none()

            invalidate_delivery_cache(delivery_result.id)
            redis_client.delete(f"{ALL_DELIVERY}")

            token = get_user_notification_token(
                db=db, user_id=delivery.vendor_id)

            # await send_push_notification(
            #     tokens=[token],
            #     title="Order canceled",
            #     message="Your Order has been canceled",
            #     navigate_to="/delivery/orders",
            # )

            return delivery_result


async def rider_accept_delivery_order(
    db: AsyncSession, delivery_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    dispatch_id = get_dispatch_id(current_user)

    result = await db.execute(
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .options(selectinload(Delivery.order))
    )
    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found."
        )

    if delivery.rider_phone_number or delivery.rider_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This order has been assigned to a rider.",
        )

    if current_user.user_type not in [UserType.RIDER, UserType.DISPATCH]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized."
        )

    await db.execute(
        update(Delivery)
        .where(Delivery.id == delivery_id)
        .values(
            {
                "delivery_status": DeliveryStatus.ACCEPT,
                "rider_id": current_user.id,
                "dispatch_id": dispatch_id,
                "rider_phone_number": current_user.profile.phone_number,
            }
        )
        .returning(Delivery.delivery_status)
    )

    # Get wallets
    dispatch_wallet = await fetch_wallet(db, delivery.dispatch_id)
    vendor_wallet = await fetch_wallet(db, delivery.vendor_id)
    sender_wallet = await fetch_wallet(db, delivery.sender_id)

    # Amount mounts to move to escrow
    dispatch_amount = delivery.amount_due_dispatch
    vendor_amount = delivery.order.amount_due_vendor
    delivery_fee = delivery.delivery_fee

    # Move dispatch funds to escrow (escrow increases)

    if delivery.delivery_type == DeliveryType.PACKAGE:
        # Move funds to escrow for dispatch
        await db.execute(
            update(Wallet)
            .where(Wallet.id == delivery.dispatch_id)
            .values(
                {"escrow_balance": dispatch_wallet.escrow_balance + dispatch_amount}
            )
        )

        # Move funds to escrow for sender
        await db.execute(
            update(Wallet)
            .where(Wallet.id == delivery.sender_id)
            .values({"escrow_balance": sender_wallet.escrow_balance + delivery_fee})
        )

        await db.commit()
        await db.refresh(delivery)

    if delivery.delivery_type in [DeliveryType.FOOD, DeliveryType.LAUNDRY]:
        # Update dispatch escrow balance
        await db.execute(
            update(Wallet)
            .where(Wallet.id == delivery.dispatch_id)
            .values(
                {"escrow_balance": dispatch_wallet.escrow_balance + dispatch_amount}
            )
        )

        # Uodate vendour escrow
        await db.execute(
            update(Wallet)
            .where(Wallet.id == delivery.vendor_id)
            .values({"escrow_balance": vendor_wallet.escrow_balance + vendor_amount})
        )

        # Update sender escrow
        await db.execute(
            update(Wallet)
            .where(Wallet.id == delivery.vendor_id)
            .values(
                {
                    "escrow_balance": sender_wallet.escrow_balance
                    + delivery.order.total_price
                }
            )
        )

        await db.commit()
        await db.refresh(delivery)

    token = get_user_notification_token(db=db, user_id=delivery.sender_id)

    # await send_push_notification(
    #     tokens=[token],
    #     title="Order Assigned",
    #     message=f"Your order has been assigned to {current_user.profile.full_name}, {current_user.profile.phone_number}",
    #     navigate_to="/delivery/orders",
    # )

    return DeliveryStatusUpdateSchema(delivery_status=delivery.delivery_status)


async def sender_confirm_delivery_received(
    db: AsyncSession, delivery_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    result = await db.execute(
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .options(selectinload(Delivery.order))
    )
    delivery = result.scalar_one_or_none()
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found.")
    if (
        current_user.user_type not in [UserType.CUSTOMER, UserType.VENDOR]
        or delivery.sender_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Unauthorized.")
    if delivery.delivery_status != DeliveryStatus.DELIVERED:
        raise HTTPException(
            status_code=400, detail="Delivery is not yet completed.")

    profile = get_user_profile(delivery.sender_id, db=db)

    sender = (
        current_user.profile.full_name
        if current_user.profile.full_name
        else current_user.profile.business_name
    )

    try:
        delivery.delivery_status = DeliveryStatus.RECEIVED

        # Get wallets
        dispatch_wallet = await fetch_wallet(db, delivery.dispatch_id)
        vendor_wallet = await fetch_wallet(db, delivery.vendor_id)
        sender_wallet = await fetch_wallet(db, delivery.sender_id)

        # Calculate amounts to release from escrow
        dispatch_amount = delivery.amount_due_dispatch or 0
        vendor_amount = delivery.order.amount_due_vendor or 0

        if delivery.delivery_type == DeliveryType.PACKAGE:
            # update wallet
            await db.execute(
                update(Wallet)
                .where(Wallet.id == delivery.dispatch_id)
                .values(
                    {
                        "balance": dispatch_wallet.balance + dispatch_amount,
                        "escrow_balance": dispatch_wallet.escrow_balance
                        - dispatch_amount,
                        "payment_by": sender,
                    }
                )
            )

            await db.execute(
                update(Wallet)
                .where(Wallet.id == delivery.sender_id)
                .values(
                    {
                        "escrow_balance": dispatch_wallet.escrow_balance
                        - dispatch_amount,
                        "payment_by": profile.full_name
                        if profile.full_name
                        else profile.business_name,
                    }
                )
            )

            await db.commit()
            await db.refresh(delivery)

            # create transaction
            await create_wallet_transaction(
                db,
                dispatch_wallet.id,
                dispatch_amount,
                TransactionType.CREDIT,
            )

            await create_wallet_transaction(
                db,
                sender_wallet.id,
                delivery.order.total_price,
                TransactionType.DEBIT,
            )

        if delivery.delivery_type in [DeliveryType.FOOD, DeliveryType.LAUNDRY]:
            # update wallet
            await db.execute(
                update(Wallet)
                .where(Wallet.id == delivery.dispatch_id)
                .values(
                    {
                        "balance": dispatch_wallet.balance + dispatch_amount,
                        "escrow_balance": dispatch_wallet.escrow_balance
                        - dispatch_amount,
                    }
                )
            )
            await db.execute(
                update(Wallet)
                .where(Wallet.id == delivery.vendor_id)
                .values(
                    {
                        "balance": vendor_wallet.balance + vendor_amount,
                        "escrow_balance": vendor_wallet.escrow_balance - vendor_amount,
                    }
                )
            )

            await db.execute(
                update(Wallet)
                .where(Wallet.id == delivery.sender_id)
                .values(
                    {
                        "escrow_balance": dispatch_wallet.escrow_balance
                        - dispatch_amount,
                    }
                )
            )

            await db.commit()
            await db.refresh(delivery)

            # create transactions
            await create_wallet_transaction(
                db,
                dispatch_wallet.id,
                dispatch_amount,
                TransactionType.CREDIT,
                payment_by=sender,
            )
            await create_wallet_transaction(
                db,
                vendor_wallet.id,
                vendor_amount,
                TransactionType.CREDIT,
                payment_by=sender,
            )

            await create_wallet_transaction(
                db,
                sender_wallet.id,
                delivery.order.total_price,
                TransactionType.DEBIT,
                payment_by=profile.full_name
                if profile.full_name
                else profile.business_name,
            )

        # redis_client.delete(f"{ALL_DELIVERY}")
        token = get_user_notification_token(db=db, user_id=delivery.rider_id)
        dispatch_token = get_user_notification_token(
            db=db, user_id=delivery.dispatch_id)
        # await send_push_notification(
        #     tokens=[token, dispatch_token],
        #     title="Order completed",
        #     message=f"Congratulations! Order completed. {delivery.amount_due_dispatch} has been released to your wallet",
        #     navigate_to="/delivery/orders",
        # )

        return DeliveryStatusUpdateSchema(delivery_status=delivery.delivery_status)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def vendor_mark_laundry_item_received(
    db: AsyncSession, delivery_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    result = await db.execute(
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .options(selectinload(Delivery.order))
    )
    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found."
        )

    if (
        current_user.user_type != UserType.VENDOR
        and current_user.id != delivery.order.vendor_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized."
        )

    if delivery.delivery_status != DeliveryStatus.DELIVERED:
        raise HTTPException(
            status_code=400, detail="Delivery is not yet completed.")

    profile = get_user_profile(delivery.sender_id, db=db)

    try:
        delivery.delivery_status = DeliveryStatus.VENDOR_RECEIVED_LAUNDRY_ITEM

        # Get wallets
        dispatch_wallet = await fetch_wallet(db, delivery.dispatch_id)

        # Calculate amounts to release from escrow
        dispatch_amount = delivery.amount_due_dispatch or 0

        # update wallet
        await db.execute(
            update(Wallet)
            .where(Wallet.id == delivery.dispatch_id)
            .values(
                {
                    "balance": dispatch_wallet.balance + dispatch_amount,
                    "escrow_balance": dispatch_wallet.escrow_balance - dispatch_amount,
                }
            )
        )

        await db.execute(
            update(Wallet)
            .where(Wallet.id == delivery.sender_id)
            .values(
                {
                    "escrow_balance": dispatch_wallet.escrow_balance - dispatch_amount,
                }
            )
        )

        await db.commit()
        await db.refresh(delivery)

        # create transactions
        await create_wallet_transaction(
            db,
            dispatch_wallet.id,
            dispatch_amount,
            TransactionType.CREDIT,
            payment_by=profile.full_name
            if profile.full_name
            else profile.business_name,
        )

        # redis_client.delete(f"{ALL_DELIVERY}")
        token = get_user_notification_token(db=db, user_id=delivery.rider_id)
        dispatch_token = get_user_notification_token(
            db=db, user_id=delivery.dispatch_id)
        # await send_push_notification(
        #     tokens=[token, dispatch_token],
        #     title="Order completed",
        #     message=f"Congratulations! Order complete. Your wallet has been credited with NGN {dispatch_amount}",
        #     navigate_to="/delivery/orders",
        # )

        return DeliveryStatusUpdateSchema(delivery_status=delivery.delivery_status)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


async def rider_mark_delivered(
    delivery_id: UUID, current_user: User, db: AsyncSession
) -> DeliveryStatusUpdateSchema:
    result = await db.execute(
        select(Delivery)
        .where(Delivery.id == delivery_id)
        .where(or_(Delivery.rider_id==current_user.id, Delivery.dispatch_id==current_user.id))
        .options(selectinload(Delivery.order))
    )

    delivery = result.scalar_one_or_none()

    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery not found.")
    if (
        current_user.user_type not in [UserType.RIDER, UserType.DISPATCH] and
        (delivery.rider_id != current_user.id or delivery.dispatch_id != current_user.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="You are not allowed to perform this action."
        )

    if delivery.delivery_status == DeliveryStatus.ACCEPT:
        await db.execute(
            update(Delivery)
            .where(Delivery.id == delivery_id)
            .values({"delivery_status": DeliveryStatus.DELIVERED})
        )

        await db.commit()
        await db.refresh(delivery)

    token = get_user_notification_token(db=db, user_id=delivery.rider_id)

    await send_push_notification(
        tokens=[token],
        title="Order Delivered",
        message=f"Your order has been delivered. Please confirm with the receipient before marking as received.",
        navigate_to="/delivery/orders",
    )

    return DeliveryStatusUpdateSchema(delivery_status=delivery.delivery_status)


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
    if current_user.get("user_type") != UserType.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied. Only ADMIN users can modify delivery status directly.",
        )

    try:
        result = await db.execute(select(Delivery).where(Delivery.id == delivery_id))
        delivery = result.scalar_one_or_none()

        if not delivery:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Delivery {delivery_id} not found.",
            )

        # Update delivery status
        await db.execute(
            update(Delivery)
            .where(Delivery.id == delivery_id)
            .values(delivery_status=DeliveryStatus.RECEIVED)
        )

        await db.commit()
        await db.refresh(delivery)

        invalidate_delivery_cache(delivery_id)
        redis_client.delete("all_deliveries")

        return DeliveryStatusUpdateSchema(delivery_status=new_status)

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update delivery status: {e}",
        )


# <<<<< ---------- ITEM REVIEWS ---------- >>>>>
async def create_review(
    db: AsyncSession, current_user: User, order_id: UUID, data: ReviewSchema
) -> Review:
    """
    Create a review for an item by a user.

    Args:
        db: The database session.
        current_user: The UUID of the reviewer.
        item_id: The UUID of the item.
        rating: The rating (1-5).
        comment: Optional review comment.

    Returns:
        The created Review object.

    Raises:
        HTTPException: If the user or item is not found, or if the rating is invalid.
    """

    order_item_ids = []

    user = await db.get(User, current_user.id)
    result = await db.execute(
        select(Order).join(Order.order_items).where(Order.id == order_id)
    )

    order = result.scalar_one_or_none()

    for item in order.order_items:
        order_item_ids.append(item.item_id, item.user_id)

    for user_id in order_item_ids:
        if current_user.id == user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot review your own item",
            )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    # Validate rating
    if not 1 <= data.rating <= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rating must be between 1 and 5",
        )

    try:
        for item_id in order_item_ids:
            review = Review(
                user_id=current_user.id,
                item_id=item_id,
                rating=data.rating,
                comment=data.comment,
            )
        db.add(review)
        await db.commit()
        await db.refresh(review)

        return review

    except IntegrityError as e:
        await db.rollback()
        if "uq_user_item_review" in str(e):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already reviewed this item",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create review: {str(e)}",
        )


# <<< ----- UTILITY FUNCTIONS FOR ORDERS/DELIVERY ----- >>>


async def get_charges(db: AsyncSession):
    result = await db.execute(select(ChargeAndCommission))
    charge = result.scalars().first()
    return charge


async def calculate_delivery_fee(distance: Decimal, db: AsyncSession) -> Decimal:
    delivery_fee = await get_charges(db)

    if distance <= 1:
        return delivery_fee.base_delivery_fee

    return distance * delivery_fee.delivery_fee_per_km


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
    payment_by: str = None,
) -> Transaction:
    """Creates a wallet transaction."""

    transx = Transaction(
        wallet_id=wallet_id,
        amount=amount,
        transaction_type=transaction_type,
        payment_by=payment_by,
    )
    db.add(transx)
    await db.flush()
    await db.refresh(transx)
    return transx


async def update_wallet_balance_and_escrow(
    db: AsyncSession, wallet_id: UUID, new_balance: Decimal, new_escrow_balance: Decimal
) -> None:
    """Updates both wallet balance and escrow balance."""
    await db.execute(
        update(Wallet)
        .where(Wallet.id == wallet_id)
        .values(balance=new_balance, escrow_balance=new_escrow_balance)
    )


async def update_wallet_balance(
    db: AsyncSession, wallet_id: UUID, new_balance: Decimal, escrow_balance: Decimal = 0
) -> None:
    """Updates a wallet's balance."""
    escrow_balance = (
        Decimal(escrow_balance - new_balance)
        if Decimal(escrow_balance) >= Decimal(new_balance)
        else Decimal(new_balance)
    )

    await db.execute(
        update(Wallet)
        .where(Wallet.id == wallet_id)
        .values(balance=new_balance, escrow_balance=Decimal(escrow_balance))
    )


async def update_wallet_escrow_balance(
    db: AsyncSession, wallet_id: UUID, new_escrow_balance: Decimal
) -> None:
    """Updates a wallet's escrow balance."""
    stmt = (
        update(Wallet)
        .where(Wallet.id == wallet_id)
        .values(escrow_balance=new_escrow_balance)
        .execution_options(synchronize_session="fetch")
    )
    await db.execute(stmt)
    await db.commit()


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
        "order_type": order.order_type.value,
        "require_delivery": order.require_delivery,
        "total_price": str(order.total_price),
        "order_payment_status": order.order_payment_status.value,
        "order_status": order.order_status.value if order.order_status else None,
        "amount_due_vendor": str(order.amount_due_vendor),
        "payment_link": order.payment_link or "",
        "order_items": order_items,
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
