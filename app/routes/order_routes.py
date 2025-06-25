from fastapi import Form, File, UploadFile, HTTPException
from uuid import UUID
import json
from decimal import Decimal

from fastapi import APIRouter, Depends, status, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_db, get_current_user
from app.models.models import User
from app.schemas.delivery_schemas import DeliveryResponse

from app.schemas.order_schema import (
    OrderAndDeliverySchema,
    OrderResponseSchema,
    PackageCreate,
    DeliveryStatusUpdateSchema,
)
from app.schemas.delivery_schemas import DeliveryStatus, DeliveryType
from app.schemas.schemas import ReviewSchema
from app.services import order_service

router = APIRouter(prefix="/api/orders", tags=["Orders"])


@router.get("", status_code=status.HTTP_200_OK)
async def get_all_orders(
    db: AsyncSession = Depends(get_db),
) -> list[DeliveryResponse]:
    return await order_service.get_all_orders(db=db)


# @router.get("/deliveries", status_code=status.HTTP_200_OK)
# async def get_deliveries(
#     db: AsyncSession = Depends(get_db),
#     skip: int = 0,
#     limit: int = 35,
#     current_user: User = Depends(get_current_user),
# ) -> list[DeliveryResponse]:
#     return await order_service.get_all_deliveries(db=db, skip=skip, limit=limit)


@router.post(
    "/send-item", response_model=DeliveryResponse, status_code=status.HTTP_201_CREATED
)
async def send_item(
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
async def order_food_or_request_laundy_service(
    vendor_id: UUID,
    order_item: OrderAndDeliverySchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryResponse:
    return await order_service.order_food_or_request_laundy_service(
        db=db, current_user=current_user, order_item=order_item, vendor_id=vendor_id
    )


@router.get("/{order_id}", status_code=status.HTTP_200_OK)
async def get_delivery_by_order_id(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> DeliveryResponse:
    return await order_service.get_delivery_by_order_id(db=db, order_id=order_id)


@router.put("/{delivery_id}/confirm-delivery", status_code=status.HTTP_202_ACCEPTED)
async def sender_confirm_delivery_received(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.sender_confirm_delivery_received(
            db=db,
            current_user=current_user,
            delivery_id=delivery_id,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/{order_id}/update-status", status_code=status.HTTP_202_ACCEPTED)
async def update_order_status(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    return await order_service.vendor_or_owner_mark_order_delivered_or_received(
        db=db,
        current_user=current_user,
        order_id=order_id,
    )


@router.put("/{delivery_id}/accept-delivery", status_code=status.HTTP_202_ACCEPTED)
async def rider_accept_delivery(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.rider_accept_delivery_order(
            db=db, current_user=current_user, delivery_id=delivery_id
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
    "/{delivery_id}/update-by-admin",
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
    "/{delivery_id}/cancel-delivery",
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_delivery(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.cancel_delivery(
            db=db, current_user=current_user, delivery_id=delivery_id
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
