import uuid
from fastapi import Body, Form, File, Request, UploadFile, HTTPException
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_db, get_current_user
from app.models.models import Order, User
from app.schemas.delivery_schemas import DeliveryResponse, PaginatedDeliveryResponse, CancelOrderSchema

from app.schemas.order_schema import (
    OrderAndDeliverySchema,
    PackageCreate,
    DeliveryStatusUpdateSchema,
)
from app.schemas.schemas import PaymentLinkSchema, ReviewSchema
from app.schemas.status_schema import OrderType, PaymentStatus
from app.services import order_service
from app.utils.limiter import limiter
from app.utils.utils import get_payment_link, get_product_payment_link
from app.config.config import redis_client

router = APIRouter(prefix="/api/orders", tags=["Orders"])


@router.get("", status_code=status.HTTP_200_OK)
async def get_all_orders(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 20,
) -> list[DeliveryResponse]:
    return await order_service.get_all_orders(db=db, skip=skip, limit=limit)


@router.get("/delivery-orders", status_code=status.HTTP_200_OK)
async def get_all_require_delivery_orders(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 20,
) -> PaginatedDeliveryResponse:
    return await order_service.get_all_require_delivery_orders(
        db=db, skip=skip, limit=limit
    )


@router.get("/pickup-orders", status_code=status.HTTP_200_OK)
async def get_all_pickup_delivery_orders(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 20,
) -> PaginatedDeliveryResponse:
    return await order_service.get_all_pickup_delivery_orders(
        db=db, skip=skip, limit=limit
    )


@router.get("/paid-pending-deliveries", status_code=status.HTTP_200_OK)
async def get_paid_pending_deliveries(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[DeliveryResponse]:
    return await order_service.get_paid_pending_deliveries(db=db, current_user=current_user)


@router.get("/{user_id}/user-related-orders", status_code=status.HTTP_200_OK)
async def get_user_related_deliveries(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[DeliveryResponse]:
    return await order_service.get_user_related_orders(db=db, user_id=user_id)


@router.post(
    "/send-item", response_model=DeliveryResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("5/minute")
async def send_item(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    distance: Decimal = Form(...),
    origin: str = Form(...),
    destination: str = Form(...),
    duration: str = Form(...),
    pickup_coordinates: str = Form(...),
    dropoff_coordinates: str = Form(...),
    image_url: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        pickup_coords = [float(x.strip()) for x in pickup_coordinates.split(",")]
        dropoff_coords = [float(x.strip()) for x in dropoff_coordinates.split(",")]

        if len(pickup_coords) != 2 or len(dropoff_coords) != 2:
            raise ValueError

    except Exception:
        raise HTTPException(
            status_code=422,
            detail="Coordinates must be two comma-separated floats like '6.45,3.40'",
        )

    form_data = PackageCreate(
        name=name,
        description=description,
        distance=distance,
        origin=origin,
        destination=destination,
        duration=duration,
        pickup_coordinates=pickup_coords,
        dropoff_coordinates=dropoff_coords,
    )

    return await order_service.create_package_order(
        db=db, current_user=current_user, data=form_data, image=image_url
    )


@router.post(
    "/{vendor_id}",
    response_model=DeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def order_food_or_request_laundy_service(
    request: Request,
    vendor_id: UUID,
    order_item: OrderAndDeliverySchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryResponse:
    return await order_service.create_food_or_laundry_order(
        db=db, current_user=current_user, order_item=order_item, vendor_id=vendor_id
    )


@router.post(
    "/{vendor_id}/laundry",
    response_model=DeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def request_laundry(
    request: Request,
    vendor_id: UUID,
    order_item: OrderAndDeliverySchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryResponse:
    return await order_service.request_laundy_service(
        db=db, current_user=current_user, order_item=order_item, vendor_id=vendor_id
    )


@router.get("/{order_id}", status_code=status.HTTP_200_OK)
async def get_delivery_by_order_id(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> DeliveryResponse:
    return await order_service.get_delivery_by_order_id(db=db, order_id=order_id)


@router.put(
    "/{order_id}/sender-confirm-delivery-or-order-received",
    status_code=status.HTTP_202_ACCEPTED,
)
async def sender_confirm_delivery_or_order_received(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.sender_confirm_delivery_or_order_received(
            db=db,
            current_user=current_user,
            order_id=order_id,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/{order_id}/vendor-update-order-status", status_code=status.HTTP_202_ACCEPTED)
async def vendor_mark_order_delivered(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    """
    Mark order delivered(for order without delivery) by vendor
    """
    return await order_service.vendor_mark_order_delivered(
        db=db,
        current_user=current_user,
        order_id=order_id,
    )


# @router.put("/{order_id}/laundry-returned", status_code=status.HTTP_202_ACCEPTED)
# async def rider_accept_delivery(
#     order_id: UUID,
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> DeliveryStatusUpdateSchema:
#     try:
#         return await order_service.laundry_return(
#             db=db, current_user=current_user, order_id=order_id
#         )
#     except Exception as e:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    

# @router.put("/{order_id}/laundry-pickup", status_code=status.HTTP_202_ACCEPTED)
# async def rider_accept_delivery(
#     order_id: UUID,
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> DeliveryStatusUpdateSchema:
#     try:
#         return await order_service.laundry_pickup(
#             db=db, current_user=current_user, order_id=order_id
#         )
#     except Exception as e:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    

@router.put("/{order_id}/accept-delivery", status_code=status.HTTP_202_ACCEPTED)
async def rider_accept_delivery(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.rider_accept_delivery_order(
            db=db, current_user=current_user, order_id=order_id
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/{delivery_id}/re-list-item", status_code=status.HTTP_202_ACCEPTED)
async def re_list_item_for_delivery(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.re_list_item_for_delivery(
            db=db, current_user=current_user, delivery_id=delivery_id
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/{delivery_id}/delivered", status_code=status.HTTP_202_ACCEPTED)
async def rider_mark_item_delivered(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.rider_mark_delivered(
            db=db, current_user=current_user, delivery_id=delivery_id
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/{delivery_id}/laundry-received", status_code=status.HTTP_202_ACCEPTED)
async def laundry_vendor_mark_item_received(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.vendor_mark_laundry_item_received(
            db=db, current_user=current_user, delivery_id=delivery_id
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/{delivery_id}/admin-update-order-status",
    status_code=status.HTTP_202_ACCEPTED,
)
async def admin_modify_order_status(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.admin_modify_order_status(
            db=db, current_user=current_user, order_id=order_id
        )

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    



@router.put(
    "/{delivery_id}/admin-update-delivery-status",
    status_code=status.HTTP_202_ACCEPTED,
)
async def admin_modify_delivery_status(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.admin_modify_delivery_status(
            db=db, current_user=current_user, delivery_id=delivery_id
        )

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/{order_id}/cancel-order-or-delivery",
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_order_or_delivery(
    order_id: UUID,
    reason: CancelOrderSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.cancel_order_or_delivery(
            db=db, current_user=current_user, order_id=order_id, reason=reason
        )

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/{order_id}/review",
    status_code=status.HTTP_201_CREATED,
)
async def add_review(
    order_id: UUID,
    data: ReviewSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ReviewSchema:
    try:
        return await order_service.create_review(
            db=db, current_user=current_user, order_id=order_id, data=data
        )

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/{order_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_order(
    order_id: UUID,
    reason: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    """
    Cancel an order (with or without delivery). Optionally provide a reason.
    """
    try:
        return await order_service.cancel_order(
            db=db, order_id=order_id, current_user=current_user, reason=reason
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))



@router.put(
    "/{order_id}/generate-new-payment-link",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_new_payment_link(
    order_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentLinkSchema:
    """
    Generate a new payment link for an order.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    try:
        if (
            order.order_type in [OrderType.FOOD, OrderType.LAUNDRY, OrderType.PACKAGE]
            and order.order_payment_status != PaymentStatus.PAID
        ):
            tx_ref = uuid.uuid1()

            try:
                # Get payment link from Flutterwave
                order_payment_link = await get_payment_link(
                    tx_ref=tx_ref, amount=order.grand_total, current_user=current_user
                )

                await db.execute(
                    update(Order)
                    .where(Order.id == order_id)
                    .values(payment_link=order_payment_link, tx_ref=tx_ref)
                )
                await db.commit()

                redis_client.delete(f"order_details:{order_id}")

                return PaymentLinkSchema(payment_link=order_payment_link)

            except Exception as payment_error:
                # Ensure we rollback any uncommitted changes
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate payment link: {str(payment_error)}",
                )

        if (
            order.order_type == OrderType.PRODUCT
            and order.order_payment_status != PaymentStatus.PAID
        ):
            tx_ref = uuid.uuid1()
            try:
                # Get payment link from Flutterwave
                order_payment_link = await get_product_payment_link(
                    id=tx_ref, amount=order.grand_total, current_user=current_user
                )

                # Update the database with the new payment link and tx_ref
                await db.execute(
                    update(Order)
                    .where(Order.id == order_id)
                    .values(payment_link=order_payment_link, tx_ref=tx_ref)
                )
                await db.commit()

                # Clear Redis cache
                redis_client.delete(f"marketplace_order_details:{order_id}")

                return PaymentLinkSchema(payment_link=order_payment_link)
            except Exception as payment_error:
                # Ensure we rollback any uncommitted changes
                await db.rollback()

                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to generate payment link: {str(payment_error)}",
                )

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
