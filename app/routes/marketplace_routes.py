from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database.database import get_db
from app.auth.auth import get_current_user
from app.models.models import User
from app.schemas.delivery_schemas import DeliveryResponse
from app.schemas.marketplace_schemas import ProductBuyRequest
from app.schemas.order_schema import OrderResponseSchema
from app.schemas.status_schema import OrderStatus
from app.services import marketplace_service
from app.schemas.item_schemas import ItemResponse
from app.utils.limiter import limiter

router = APIRouter(prefix="/api/marketplace", tags=["Marketplace"])


@router.get(
    "",
    response_model=list[ItemResponse],
    status_code=status.HTTP_200_OK,
    summary="Get marketplace items",
)
async def get_marketplace_items(
    db: AsyncSession = Depends(get_db),
):
    return await marketplace_service.get_marketplace_items(db=db)


@router.get(
    "/{user_id}/user-orders",
    response_model=list[DeliveryResponse],
    status_code=status.HTTP_200_OK,
)
async def get_user_product_orders(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    return await marketplace_service.get_user_orders(db=db, user_id=user_id)


@router.get(
    "/{item_id}/marketplace",
    response_model=ItemResponse,
    status_code=status.HTTP_200_OK,
    summary="Get marketplace item",
)
async def get_marketplace_item(
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await marketplace_service.get_marketplace_item(db=db, item_id=item_id)


@router.post(
    "/{product_id}/buy",
    response_model=OrderResponseSchema,
    status_code=status.HTTP_200_OK,  # 200 OK for successful purchase
    summary="Buy a product",
    description="Allows an authenticated user to purchase a specified quantity of a product.",
)
@limiter.limit("5/minute")
async def buy_listed_product(
    request: Request,
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
    return await marketplace_service.buy_product(
        db=db,
        product_id=product_id,
        buyer=current_user,
        buy_request=buy_request,
    )


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
    return await marketplace_service.vendor_mark_item_delivered(
        order_id=order_id, current_user=current_user, db=db
    )


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
    return await marketplace_service.owner_mark_item_received(
        order_id=order_id, current_user=current_user, db=db
    )


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
    return await marketplace_service.owner_mark_item_rejected(
        order_id=order_id, current_user=current_user, db=db
    )


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
    return await marketplace_service.vendor_mark_rejected_item_received(
        order_id=order_id, current_user=current_user, db=db
    )
