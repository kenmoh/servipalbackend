from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_current_user
from app.database.database import get_db
from app.models.models import User
from app.schemas.marketplace_schemas import TopUpRequestSchema
from app.services import transaction_service


router = APIRouter(prefix="/api/payment", tags=["Payments"])


@router.get("/order-payment-callback", status_code=status.HTTP_200_OK)
async def order_payment_callback(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    return await transaction_service.order_payment_callback(request=request, db=db)


@router.get("/fund-wallet-callback", status_code=status.HTTP_200_OK)
async def fund_wallet_payment_callback(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    return await transaction_service.fund_wallet_callback(request=request, db=db)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def process_webhook(
    request: Request,
    background_task: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    return await transaction_service.handle_payment_webhook(
        request=request, db=db, background_task=background_task
    )


@router.post("/fund-wallet", status_code=status.HTTP_200_OK)
async def fund_wallet(
    data: TopUpRequestSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    return await transaction_service.top_up_wallet(
        topup_data=data, db=db, current_user=current_user
    )
