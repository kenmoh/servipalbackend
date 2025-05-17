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


@router.get("/deliveries", status_code=status.HTTP_200_OK)
async def get_deliveries(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 20,
    delivery_type: DeliveryType = None,
    current_user: User = Depends(get_current_user),
) -> list[DeliveryResponse]:
    return await order_service.get_all_deliveries(db=db, skip=skip, limit=limit)


@router.get("/delivery-by-type", status_code=status.HTTP_200_OK)
async def filter_deliveries_by_type(
    delivery_type: DeliveryType,
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
) -> list[DeliveryResponse]:
    return await order_service.filter_deliveries_by_type(
        delivery_type=delivery_type, db=db, skip=skip, limit=limit
    )


# @router.post(
#     "/send-item",
#     response_model=DeliveryResponse,
#     status_code=status.HTTP_201_CREATED,
# )
# async def send_item(
#     data: PackageCreate = Form(..., media_type='multipart/form-data'),
#     # image: UploadFile = File(...),
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ) -> DeliveryResponse:

    
#     parsed_data = json.loads(data)
#     return await order_service.create_package_order(
#         db=db, current_user=current_user, data=data
#     )


from fastapi import Form, File, UploadFile, HTTPException

@router.post("/send-item", response_model=DeliveryResponse, status_code=status.HTTP_201_CREATED)
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


@router.get("/{order_id}/summary", status_code=status.HTTP_200_OK)
async def get_order_details(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrderResponseSchema:
    return await order_service.get_order_with_items(db, order_id)


@router.get("/{delivery_id}", status_code=status.HTTP_200_OK)
async def get_delivery_by_id(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryResponse:
    return await order_service.get_delivery_by_id(db=db, delivery_id=delivery_id)


@router.put("/{delivery_id}/confirm-delivery", status_code=status.HTTP_202_ACCEPTED)
async def confirm_delivery_received(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.confirm_delivery_received(
            db=db,
            current_user=current_user,
            delivery_id=delivery_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put(
    "/{delivery_id}/update-delivery-status", status_code=status.HTTP_202_ACCEPTED
)
async def rider_update_delivery_status(
    delivery_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatusUpdateSchema:
    try:
        return await order_service.rider_update_delivery_status(
            db=db, current_user=current_user, delivery_id=delivery_id
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
