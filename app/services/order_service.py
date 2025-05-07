from datetime import timedelta, datetime
from typing import Optional
from sqlalchemy import or_, select, update, insert
from sqlalchemy.orm import joinedload, selectinload
from app.models.models import (
    ChargeAndCommission,
    Delivery,
    Item,
    Order,
    OrderItem,
    Transaction,
    User,
    Wallet,
    ItemImage
)

import json
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.status_schema import OrderStatus

from app.schemas.order_schema import (

    PaymentStatus,
    OrderItemResponseSchema,
    OrderResponseSchema,

    PackageCreate,

    WalletTransactionType,
    DeliveryStatusUpdateSchema,
    ItemImageSchema
)
from app.schemas.delivery_schemas import DeliveryResponse, DeliveryType, DeliveryStatus, DeliverySchema
from app.schemas.item_schemas import ItemType

from app.schemas.status_schema import RequireDeliverySchema
from app.schemas.user_schemas import TransactionSchema, UserType, WalletRespose
from app.utils.utils import (
    get_dispatch_id,
    get_payment_link,
)
from app.config.config import redis_client


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
                joinedload(Order.delivery)  # This loads the order's delivery
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
        json.dumps(delivery_response.model_dump(), default=str)
    )

    return delivery_response


async def get_all_deliveries(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20
) -> list[DeliveryResponse]:
    """
    Get all deliveries with pagination and caching
    """
    cache_key = f"all_deliveries:{skip}:{limit}"

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
                        selectinload(Item.images)
                    )
                ),
                joinedload(Order.delivery)
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
        format_delivery_response(delivery.order, delivery)
        for delivery in deliveries
    ]

    # Cache the formatted responses
    redis_client.setex(
        cache_key,
        timedelta(seconds=CACHE_TTL),
        json.dumps([d.model_dump() for d in delivery_responses], default=str)
    )

    return delivery_responses


async def create_package_order(
    db: AsyncSession, data: PackageCreate, current_user: User
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
            insert(Item.__table__)
            .values(
                {
                    "user_id": current_user.id,
                    "item_type": ItemType.PACKAGE,
                    "name": data.name,
                    "description": data.description,

                }
            )
            .returning(
                Item.__table__.c.name, Item.__table__.c.id, Item.__table__.c.user_id
            )
        )

        package_data = package_insert_result.fetchone()

        if hasattr(data, 'image_urls') and data.image_urls:
            image_payloads = [
                {
                    "item_id": package_data.id,
                    "url": image_url,
                    "created_at": datetime.now(),
                    "updated_at": datetime.now()
                }
                for image_url in data.image_urls
            ]

            if image_payloads:
                await db.execute(insert(ItemImage.__table__).values(image_payloads))

        order_insert_result = await db.execute(
            insert(Order.__table__)
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
            .returning(
                Order.__table__.c.id, Order.__table__.c.id, Order.__table__.c.owner_id
            )
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
        await db.execute(insert(OrderItem.__table__).values(package_item_payload))

        # --- 3. Calculate Delivery Fee (Needs distance!) ---
        delivery_fee = await calculate_delivery_fee(data.distance, db)
        amount_due_dispatch = await calculte_amount_due_dispatch(db, delivery_fee)

        # --- 4. Insert into 'deliveries' table ---

        delivery_insert_result = await db.execute(
            insert(Delivery.__table__)
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
                    "distance": data.distance

                }
            )
            .returning(
                Delivery.__table__.c.id,
                Delivery.__table__.c.order_id,
                Delivery.__table__.c.delivery_fee,
                Delivery.__table__.c.vendor_id,
            )
        )

        delivery_data = delivery_insert_result.fetchone()

        # --- 5. Generate Payment Link ---
        total_amount_due = delivery_data.delivery_fee

        payment_link = await get_payment_link(
            id=delivery_data.id,
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
        if hasattr(delivery_data, 'vendor_id'):
            redis_client.delete(f"vendor_orders:{delivery_data.vendor_id}")
        else:
            redis_client.delete(f"vendor_orders:{current_user.id}")

        stmt = select(Order).where(Order.id == delivery_data.order_id).options(
            selectinload(Order.order_items).options(
                joinedload(OrderItem.item).options(
                    selectinload(Item.images)
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
    order_item: list[OrderItem],
) -> DeliveryResponse:
    """
    Creates a meal or linen order and its associated delivery record.
    Args:
        current_user: The user creating the order.
        db: AsyncSession  client instance.
        vendor_id: The ID of the vendor for the order.
        order_item: The order item details including items and delivery info.
    Returns:
        OrderResponseSchema: The created order and delivery details.
    Raises:
        HTTPException: If any validation fails or if the order creation fails.
    """

    # 1. Determine Order Type & Validate Vendor
    # Uses the first item to guess if it's a meal ('menu') or laundry ('linens') order

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

    order_id = None

    # Ensure all items in the order belong to the specified vendor_id
    for item in order_item.order_items:
        if item.vendor_id != vendor_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="All items must belong to the same vendor",
            )

    # 2. Calculate Total Price of Items
    total_price = Decimal("0.00")
    item_type = None  # Initialize as Decimal

    for item in order_item.order_items:
        # Fetch each item's price
        item_data = await db.execute(
            select(Item).where(Item.id == item.item_id)
        )
        if not item_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Item not found"
            )

        total_price += Decimal(item_data["price"]) * Decimal(item.quantity)
        item_type = item_data.item_type

    # 3. Calculate Amount Due to Vendor (after commission)

    amount_due_vendor = calculate_amount_due_vendor(db, order_item.order_items)

    try:
        # 4. Create the Order Record
        insert_result = await db.execute(
            insert(Order.__table__)
            .values(
                {
                    "user_id": current_user.id,
                    "vendor_id": vendor_id,
                    "order_type": item_type,
                    "total_price": total_price,
                    "order_payment_status": PaymentStatus.PENDING,
                    "amount_due_vendor": amount_due_vendor,
                }
            )
            .returning(
                Order.__table__.c.id,
                Order.__table__.c.vendor_id,
                Order.__table__.c.user_id,
            )
        )
        # order_insert_result = insert_result.scalar_one_or_none()

        order = insert_result.fetchone()

        # 5. Add Order Items to Junction Tables
        order_items_payload = []

        for item in order_item.order_items:
            order_items_payload.append(
                {
                    "order_id": order.id,
                    "item_id": item.item_id,
                    "quantity": item.quantity,
                }
            )
        # Insert all items in one go
        await db.execute(select(OrderItem).insert(order_items_payload))

        # 6. Calculate Delivery Fees
        delivery_fee = await calculate_delivery_fee(order_item.distance, db)

        # Calculate amount due to dispatch
        amount_due_dispatch = await calculte_amount_due_dispatch(db, delivery_fee)

        # 7. Create the Delivery Record
        await db.execute(
            insert(Delivery).values(
                {
                    "order_id": order.id,
                    "sender_id": current_user.id,
                    "delivery_type": item_type,
                    "delivery_status": DeliveryStatus.PENDING,
                    "pickup_coordinates": order_item.pickup_coordinates,
                    "dropoff_coordinates": order_item.dropoff_coordinates,
                    "distance": Decimal(order_item.distance),
                    "duration": Decimal(order_item.duration),
                    "delivery_fee": delivery_fee,
                    "amount_due_dispatch": amount_due_dispatch,
                }
            )
        )

        # 8. Calculate Final Amount & Generate Payment Link
        final_amount_due = total_price + delivery_fee
        payment_link = get_payment_link(
            order.id, final_amount_due, current_user)

        # 9. Update Order with Payment Link
        await db.execute(
            update(Order)
            .where(Order.id == order.id)
            .values({"payment_link": payment_link})
        )
        await db.commit()

        user_id = current_user.id
        redis_client.delete(f"user_orders:{user_id}")
        redis_client.delete(f"vendor_orders:{vendor_id}")
        redis_client.delete(f"order_details:{order_id}")

        return DeliveryResponse

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


async def update_delivery_status(
    db: AsyncSession, delivery_id: UUID, current_user: User, _status: DeliveryStatus
) -> DeliveryResponse:
    """Updates the status of a delivery and handles related financial transactions.

    Args:
        supabase: Supabase client instance.
        delivery_id: UUID of the delivery to update.
        current_user: Dictionary containing the current user's information.
        _status: New delivery status.

    Returns:
        The updated delivery data.

    Raises:
        HTTPException: If the delivery is not found or if the user does not have permission.
    """
    delivery = await fetch_delivery_by_id(db, delivery_id)

    dispatch_id = get_dispatch_id(current_user)

    dispatch_wallet = await fetch_wallet(db, delivery.dispatch_id)
    vendor_wallet = await fetch_wallet(db, delivery.vendor_id)

    # Get Escrow balance
    dispatch_escrow_balance = dispatch_wallet.escrow_balance or 0
    vendor_escrow_balance = vendor_wallet.escrow_balance or 0

    if current_user.user_type in [UserType.RIDER, UserType.DISPATCH] and not (
        current_user.profile.full_name or current_user.profile.phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number and full name are required. Please update your profile!",
        )

    if current_user.user_type == UserType.CUSTOMER:
        if delivery.sender_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Delivery not found",
            )

        if delivery.delivery_status != DeliveryStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This delivery is still in transit.",
            )

        await update_delivery_status_in_db(
            db=db, delivery_id=delivery_id, status=DeliveryStatus.RECEIVED
        )

        dispatch_wallet_transaction = await create_wallet_transaction(
            db,
            dispatch_wallet.id,
            delivery.amount_due_dispatch or 0,
            WalletTransactionType.DEPOSIT,
            current_user.profile.full_name,
        )
        await update_wallet_balance(
            db=db,
            wallet_id=dispatch_wallet.id,
            new_balance=dispatch_wallet.balance + dispatch_wallet_transaction.amount,
            escrow_balance=dispatch_escrow_balance
        )

        vendor_wallet_transaction = create_wallet_transaction(
            db,
            vendor_wallet.id,
            delivery.order.amount_due_vendor or 0,
            WalletTransactionType.DEPOSIT,
            current_user.profile.full_name,
        )

        await update_wallet_balance(
            db=db,
            wallet_id=vendor_wallet.id,
            new_balance=vendor_wallet.balance + vendor_wallet_transaction.amount,
            escrow_balance=dispatch_escrow_balance
        )

        return DeliveryStatusUpdateSchema(**delivery.delivery_status)

    if current_user.user_type in [UserType.DISPATCH, UserType.RIDER]:
        # Handle the specific transition from PENDING
        if delivery.delivery_status == DeliveryStatus.PENDING:
            # Ensure the requested status is IN_TRANSIT for this initial step
            if _status != DeliveryStatus.IN_TRANSIT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="First status update for a PENDING delivery by rider/dispatch must be IN_TRANSIT",
                )

            await db.execute(
                update(Delivery)
                .where(Delivery.id == delivery_id)
                .values(
                    delivery_status=DeliveryStatus.IN_TRANSIT,
                    rider_id=current_user.id,
                    dispatch_id=dispatch_id,
                )
            )
            # Refresh the delivery object to reflect the change for subsequent logic
            await db.refresh(
                delivery, attribute_names=[
                    "delivery_status", "rider_id", "dispatch_id"]
            )

        # Handle other status updates or check permissions for non-PENDING states
        elif (
            delivery.dispatch_id != dispatch_id or delivery.rider_id != current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to update this delivery's status.",
            )
        elif delivery.delivery_status != _status:
            # If status is not PENDING and needs changing to _status
            await update_delivery_status_in_db(db, delivery_id, _status)
            # Refresh status
            await db.refresh(delivery, attribute_names=["delivery_status"])

        # dispatch_wallet = fetch_wallet(db, delivery.dispatch_id)
        # vendor_wallet = fetch_wallet(db, delivery.vendor_id)

        # dispatch_escrow_balance = dispatch_wallet.escrow_balance or 0
        # vendor_escrow_balance = vendor_wallet.escrow_balance or 0

        if _status in [
            DeliveryStatus.IN_TRANSIT,
            DeliveryStatus.LAUNDRY_DELIVERES_TO_VENDORR,
            DeliveryStatus.COMPLETED,
        ]:
            dispatch_new_escrow_balance = (
                dispatch_escrow_balance + delivery.amount_due_dispatch or 0
            )

            vendor_new_escrow_balance = (
                vendor_escrow_balance + delivery.amount_due_vendor or 0
            )
            await update_wallet_escrow_balance(
                db, delivery.dispatch_id, dispatch_new_escrow_balance
            )

            await update_wallet_escrow_balance(
                db, delivery.vendor_id, vendor_new_escrow_balance
            )

        # --- Cache Invalidation ---
        invalidate_order_cache(delivery.order_id)
        redis_client.delete(f"user_orders:{delivery.sender_id}")

        if delivery.dispatch_id:
            redis_client.delete(f"dispatch_deliveries:{delivery.dispatch_id}")

        if delivery.rider_id:
            redis_client.delete(f"rider_deliveries:{delivery.rider_id}")

        return DeliveryStatusUpdateSchema(**delivery.delivery_status)


# Return None if formatting fails for an order


# <<< ----- CANCEL DELIVERY(RIDER/DISPATCH/CUSTOMER)
async def cancel_delivery(
    db: AsyncSession, delivery_id: UUID, current_user: User
) -> DeliveryStatusUpdateSchema:
    """
    cancel delivery(Owner/Rider/Dispatch)
    """

    if current_user.user_type not in [
        UserType.RIDER,
        UserType.DISPATCH,
        UserType.CUSTOMER,
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    if current_user.user_type == UserType.CUSTOMER:
        result = await db.execute(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .where(Delivery.sender_id == current_user.id)
        )
        delivery = result.scalar_one_or_none()

        if delivery.delivery_status not in [
            DeliveryStatus.IN_TRANSIT,
            DeliveryStatus.COMPLETED,
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
                )
                .where(Delivery.id == delivery_id)
                .where(Delivery.rider_id == current_user.id)
            )

            await db.commit()
            delivery_result = result.scalar_one_or_none()
            invalidate_delivery_cache(delivery_result.id)
            return delivery_result


# <<<--- admin_modify_delivery_status --->>>
async def admin_modify_delivery_status(
    db: AsyncSession, delivery_id: UUID, new_status: DeliveryStatus, current_user: dict
) -> DeliveryResponse:
    """
    Allows an ADMIN user to forcibly change the status of any delivery.

    Args:
        supabase: Supabase client instance.
        delivery_id: The UUID of the delivery to modify.
        new_status: The new DeliveryStatus enum value to set.
        current_user: The user performing the action (must be ADMIN).

    Returns:
        The updated DeliveryResponse object.

    Raises:
        HTTPException: If user is not ADMIN, delivery not found, or update fails.
    """
    # --- 1. Permission Check ---
    if current_user.get("user_type") != UserType.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied. Only ADMIN users can modify delivery status directly.",
        )

    # --- 2. Fetch Delivery (to ensure it exists) ---
    try:
        # Fetch enough details to return a DeliveryResponse later
        result = await db.execute(select(Delivery).where(Delivery.id == delivery_id))

        delivery_data = result.scalar_one_or_none()

        if not delivery_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Delivery {delivery_id} not found.",
            )
        await update_delivery_status_in_db(db, delivery_id, new_status)
        invalidate_delivery_cache(delivery_data.id)
        return DeliveryStatusUpdateSchema
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed update delivery status: {e}",
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


async def calculte_amount_due_dispatch(
    db: AsyncSession, delivery_fee: Decimal
) -> Decimal:
    _delivery_commission = await get_charges(db)

    return delivery_fee - (
        delivery_fee * _delivery_commission.delivery_commission_percentage
    )


async def calculate_amount_due_vendor(
    db: AsyncSession, order_items: list[dict]
) -> Decimal:
    # table_name = determine_delivery_type(order_items)
    total_price = Decimal("0.00")
    for item in order_items:
        # fetch each item price (meal or linen service)
        result = await db.execute(
            select(Item.__table__).where(Item.__table__.c.id == item["id"])
        )
        item_data = result.scalar_one_or_none()

        if not item_data:
            raise Exception("Invalid item selected")

        total_price += Decimal(item_data["price"]) * item["quantity"]

    # 3. Calculate commission
    # Fetch commission config
    charge = await get_charges(db)
    return total_price - (total_price * charge["food_laundry_commission_percentage"])


async def fetch_wallet(db: AsyncSession, user_id: UUID) -> WalletRespose:
    """Fetches a wallet for a user."""
    result = await db.execute(
        select(Wallet.__table__).where(Wallet.__table__.c.id == user_id)
    )
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
    transaction_type: WalletTransactionType,
    username: str,
) -> TransactionSchema:
    """Creates a wallet transaction."""
    result = await db.execute(
        select(Transaction.__table__)
        .where(Transaction.__table__.c.wallet_id == wallet_id)
        .insert(
            {
                "username": username or None,
                "wallet_id": wallet_id,
                "amount": amount,
                "transaction_type": transaction_type,
            }
        )
    )

    return result.scalar_one_or_none()


async def update_wallet_balance(
    db: AsyncSession, wallet_id: UUID, new_balance: Decimal, escrow_balance: Decimal = 0
) -> WalletRespose:
    """Updates a wallet's balance."""
    escrow_balance = Decimal(
        escrow_balance - new_balance) if Decimal(escrow_balance) >= Decimal(new_balance) else Decimal(new_balance)
    await db.execute(
        select(Wallet.__table__)
        .where(Wallet.__table__.c.idd == wallet_id)
        .update({"balance": new_balance, 'escrow_balance': Decimal(escrow_balance)})
    )


async def update_wallet_escrow_balance(
    db: AsyncSession, wallet_id: UUID, new_escrow_balance: Decimal
) -> None:
    """Updates a wallet's escrow balance."""
    await db.execute(
        select(Wallet.__table__)
        .where(Wallet.__table__.c.id == wallet_id)
        .update({"escrow_balance": new_escrow_balance})
    )


async def update_delivery_status_in_db(
    db: AsyncSession, delivery_id: UUID, status: DeliveryStatus
) -> dict:
    """Updates the delivery status in the database."""
    result = await db.execute(
        update(Delivery.__table__)
        .where(Delivery.__table__.c.id == delivery_id)
        .values({"delivery_status": status})
    )
    return result.scalar_one_or_none()


async def fetch_delivery_by_id(db: AsyncSession, delivery_id: UUID) -> DeliveryResponse:
    """Fetches a delivery by its ID."""
    result = await db.execute(
        (Delivery.__table__).where(Delivery.__table__.c.id == delivery_id)
    )
    delivery = result.scalar_one_or_none()
    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found"
        )
    return delivery


def format_delivery_response1(
    order: Order,
    delivery: Optional[Delivery] = None,
) -> DeliveryResponse:
    """
    Format SQLAlchemy Order and Delivery objects into a standardized DeliveryResponse.

    Args:
        order: SQLAlchemy Order model instance
        delivery: Optional SQLAlchemy Delivery model instance

    Returns:
        DeliveryResponse object with properly formatted data
    """
    # Process order items
    order_items = []
    for order_item in order.order_items:
        item = order_item.item
        images = [
            ItemImageSchema(
                id=image.id,
                item_id=image.item_id,
                url=image.url
            ) for image in item.images
        ]

        order_items.append(
            OrderItemResponseSchema(
                id=item.id,
                user_id=item.user_id,
                name=item.name,
                price=item.price,
                images=images,
                description=item.description or "",
                quantity=order_item.quantity
            )
        )

    # Create OrderResponseSchema
    order_schema = OrderResponseSchema(
        id=order.id,
        user_id=order.owner_id,
        vendor_id=order.vendor_id,
        order_type=order.order_type.value,
        total_price=order.total_price,
        order_payment_status=order.order_payment_status.value,
        order_status=order.order_status.value if order.order_status else None,
        amount_due_vendor=order.amount_due_vendor,
        payment_link=order.payment_link or "",
        order_items=order_items
    )

    # Create DeliverySchema if delivery exists
    delivery_schema = None
    if delivery:
        delivery_schema = DeliverySchema(
            id=delivery.id,
            delivery_type=delivery.delivery_type.value,
            delivery_status=delivery.delivery_status.value,
            sender_id=delivery.sender_id,
            vendor_id=delivery.vendor_id,
            dispatch_id=delivery.dispatch_id,
            distance=delivery.distance,
            delivery_fee=delivery.delivery_fee,
            amount_due_dispatch=delivery.amount_due_dispatch,
            created_at=delivery.created_at
        )

    return DeliveryResponse(
        delivery=delivery_schema,
        order=order_schema
    )


def format_delivery_response2(
    order: Order,
    delivery: Optional[Delivery] = None,
) -> DeliveryResponse:
    """
    #Format SQLAlchemy Order and Delivery objects into a standardized DeliveryResponse.

    #Args:
        #order: SQLAlchemy Order model instance
        #delivery: Optional SQLAlchemy Delivery model instance
        #distance: Optional distance information if available

    #Returns:
        #DeliveryResponse object with properly formatted data
    """

    # Convert Order to expected dictionary structure
    order_dict = {
        "id": str(order.id),
        "user_id": str(order.owner_id),
        "vendor_id": str(order.vendor_id),
        "order_type": order.order_type,
        "total_price": str(order.total_price),
        "order_payment_status": order.order_payment_status,
        "order_status": order.order_status,
        "amount_due_vendor": str(order.amount_due_vendor),
        "payment_link": order.payment_link,
        "order_items": []
    }

    # Process order items and their images
    for item_rel in order.order_items:
        item = item_rel.item
        item_dict = {
            "id": str(item.id),
            "user_id": str(item.user_id),
            "name": item.name,
            "price": str(item.price),
            "description": item.description,
            "quantity": item_rel.quantity,
            "images": []
        }

        # Add images if they exist
        if hasattr(item, 'images') and item.images:
            image_data = {
                "id": str(item.images[0].id) if item.images else None,
                "item_id": str(item.id),
                "url": [img.url for img in item.images]
            }
            item_dict["image_url"] = [image_data]

        order_dict["order_items"].append(item_dict)

    # Convert Delivery to expected dictionary structure if available
    delivery_dict = None
    if delivery:
        delivery_dict = {
            "id": delivery.id,
            "delivery_type": delivery.delivery_type,
            "delivery_status": delivery.delivery_status,
            "sender_id": delivery.sender_id,
            "vendor_id": delivery.vendor_id,
            "dispatch_id": str(delivery.dispatch_id) if hasattr(delivery, "dispatch_id") and delivery.dispatch_id else None,
            "distance": str(delivery.distance),
            # "duration": getattr(delivery, "duration", None),
            "delivery_fee": delivery.delivery_fee,
            "amount_due_dispatch": delivery.amount_due_dispatch,
            "created_at": delivery.created_at
        }

    # Create and return the response
    order_schema = OrderResponseSchema.model_validate(order_dict)
    delivery_schema = DeliverySchema.model_validate(
        delivery_dict) if delivery_dict else None

    return DeliveryResponse(
        delivery=delivery_schema,
        order=order_schema
    )


def format_delivery_response(order: Order, delivery: Optional[Delivery] = None) -> DeliveryResponse:
    # Format order items with proper image structure
    order_items = []
    for order_item in order.order_items:
        item = order_item.item
        images = [
            {
                "id": str(image.id),
                "item_id": str(image.item_id),
                "url": image.url
            }
            for image in item.images
        ]

        order_items.append({
            "id": str(item.id),
            "user_id": str(item.user_id),
            "name": item.name,
            "price": str(item.price),
            "images": images,  # Properly structured
            "description": item.description or "",
            "quantity": order_item.quantity
        })

    # Format delivery if exists
    delivery_data = None
    if delivery:
        delivery_data = {
            "id": str(delivery.id),
            "delivery_type": delivery.delivery_type.value,
            "delivery_status": delivery.delivery_status.value,
            "sender_id": str(delivery.sender_id),
            "vendor_id": str(delivery.vendor_id),
            "dispatch_id": str(delivery.dispatch_id) if delivery.dispatch_id else None,
            "distance": str(delivery.distance),
            "delivery_fee": str(delivery.delivery_fee),
            "amount_due_dispatch": str(delivery.amount_due_dispatch),
            "created_at": delivery.created_at.isoformat()
        }

    # Format order
    order_data = {
        "id": str(order.id),
        "user_id": str(order.owner_id),
        "vendor_id": str(order.vendor_id),
        "order_type": order.order_type.value,
        "total_price": str(order.total_price),
        "order_payment_status": order.order_payment_status.value,
        "order_status": order.order_status.value if order.order_status else None,
        "amount_due_vendor": str(order.amount_due_vendor),
        "payment_link": order.payment_link or "",
        "order_items": order_items
    }

    return DeliveryResponse(
        delivery=delivery_data,
        order=order_data
    )


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


"""

  "delivery": {
    "distance": "string",
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "delivery_type": "meal",
    "delivery_status": "pending",
    "sender_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "vendor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "dispatch_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "name": "string",
    "image_url": "string",
    "duration": "string",
    "delivery_fee": "string",
    "amount_due_dispatch": "string",
    "created_at": "2025-05-05T11:03:43.084Z"
  },
  "order": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "vendor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "order_type": "string",
    "total_price": "string",
    "order_payment_status": "string",
    "order_status": "string",
    "amount_due_vendor": "string",
    "payment_link": "string",
    "order_items": [
      {
        "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "user_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "name": "string",
        "price": "string",
        "image_url": "string",
        "description": "string",
        "quantity": 0
      }
    ]
  }
}
"""
