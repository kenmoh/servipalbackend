from uuid import UUID


from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_db, get_current_user
from app.models.models import User
from app.schemas.delivery_schemas import DeliveryResponse

from app.schemas.order_schema import OrderItem, OrderResponseSchema, PackageCreate
from app.schemas.delivery_schemas import DeliveryStatus
from app.services import order_service

router = APIRouter(prefix="/api/orders", tags=["Orders"])


@router.post(
    "/send-item",
    response_model=DeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_item(
    data: PackageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryResponse:
    return await order_service.create_package_order(
        db=db, current_user=current_user, data=data
    )


@router.post(
    "/{vendor_id}",
    response_model=DeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def order_food_or_request_laundy_service(
    vendor_id: UUID,
    order_items: list[OrderItem],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryResponse:
    return await order_service.order_food_or_request_laundy_service(
        db=db, current_user=current_user, order_items=order_items, vendor_id=vendor_id
    )


@router.get(
    "/{order_id}/summary",
)
async def get_order_details(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OrderResponseSchema:
    return await order_service.get_order_with_items(db, order_id)


@router.put(
    "/{delivery_id}",
)
async def update_delivery_status(
    delivery_id: UUID,
    status: DeliveryStatus,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeliveryStatus:
    return await order_service.update_delivery_status(
        db=db, current_user=current_user, delivery_id=delivery_id, _status=status
    )
