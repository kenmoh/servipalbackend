from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database.database import get_db
from app.auth.auth import get_current_user
from app.models.models import Order, User
from app.schemas.delivery_schemas import DeliveryResponse
from app.schemas.marketplace_schemas import ProductBuyRequest, ProductOrderResponse
from app.schemas.schemas import PaymentLinkSchema
from app.schemas.status_schema import ProdductOrderStatusResponse
from app.services import marketplace_service
from app.schemas.item_schemas import ItemResponse
from app.utils.limiter import limiter
from app.utils.utils import get_product_payment_link
from app.config.config import redis_client

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
    "/{order_id}/product-order-details",
    response_model=ProductOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Get marketplace order detail",
)
async def get_product_order_details(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    return await marketplace_service.get_product_order_details(db=db, order_id=order_id)

@router.get(
    "/{user_id}/user-orders",
    response_model=list[ProductOrderResponse],
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
    response_model=ProductOrderResponse,
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
    response_model=ProdductOrderStatusResponse,
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
    response_model=ProdductOrderStatusResponse,
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
    response_model=ProdductOrderStatusResponse,
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
    response_model=ProdductOrderStatusResponse,
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



@router.put(
    "/{order_id}/generate-new-payment-link",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_new_payment_link(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
   
) -> PaymentLinkSchema:
    """
   Generate a new payment link for an order.
    """
    order = await db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    
    try:


        order_payment_link = await get_product_payment_link(id=order_id, amount=order.grand_total, db=db)

        order.payment_link = order_payment_link
        await db.commit()

        redis_client.delete(f"marketplace_order_details:{order_id}")

        return PaymentLinkSchema(payment_link=order_payment_link)



    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))