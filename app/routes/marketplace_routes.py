from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database.database import get_db
from app.auth.auth import get_current_user
from app.models.models import User
from app.schemas.marketplace_schemas import ProductBuyRequest
from app.schemas.order_schema import OrderResponseSchema
from app.schemas.status_schema import OrderStatus
from app.services import marketplace_service

router = APIRouter(prefix="/marketplace", tags=["Marketplace"])


@router.post(
    "/{product_id}/buy",
    response_model=OrderResponseSchema,
    status_code=status.HTTP_200_OK,  # 200 OK for successful purchase
    summary="Buy a product",
    description="Allows an authenticated user to purchase a specified quantity of a product.",
)
async def buy_listed_product(
    product_id: UUID,
    buy_request: ProductBuyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Endpoint for a user to buy a product.

    - Requires authentication.
    - Checks product availability, stock, and user funds.
    - Creates a transaction record and updates stock/wallet balances.
    """
    transaction = await marketplace_service.buy_product(
        db=db,
        product_id=product_id,
        buyer=current_user,
        buy_request=buy_request,
    )
    # Exceptions (404, 400, 500) are handled within the service layer
    return transaction


@router.put(
    "/{order_id}/item-delivered",
    response_model=OrderStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update order status",
)
async def vendor_mark_item_delivered(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updated_order = await marketplace_service.vendor_mark_item_delivered(
        order_id=order_id, current_user=current_user, db=db
    )
    return updated_order


@router.put(
    "/{order_id}/item-received",
    response_model=OrderStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update order status",
)
async def owner_mark_item_received(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updated_order = await marketplace_service.owner_mark_item_received(
        order_id=order_id, current_user=current_user, db=db
    )
    return updated_order


@router.put(
    "/{order_id}/item-rejected",
    response_model=OrderStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update order status",
)
async def owner_mark_item_rejected(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updated_order = await marketplace_service.owner_mark_item_rejected(
        order_id=order_id, current_user=current_user, db=db
    )
    return updated_order


@router.put(
    "/{order_id}/vendor-received-rejected-item",
    response_model=OrderStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update order status",
)
async def vendor_mark_rejected_item_received(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updated_order = await marketplace_service.vendor_mark_rejected_item_received(
        order_id=order_id, current_user=current_user, db=db
    )
    return updated_order
