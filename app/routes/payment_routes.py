from uuid import UUID
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.auth import get_current_user
from app.database.database import get_db
from app.models.models import Transaction, User, Wallet, Order
from app.schemas.marketplace_schemas import (
    TopUpRequestSchema,
    TransferDetailResponseSchema,
    WithdrawalShema,
    TopUpResponseSchema,
)
from app.schemas.schemas import GenerateLinkType, PaymentLinkSchema
from app.services import transaction_service
from app.schemas.transaction_schema import (
    TransactionSchema,
    TransactionFilterSchema,
    TransactionResponseSchema,
)
from app.schemas.status_schema import TransactionType, PaymentStatus, PaymentMethod
from app.utils.utils import get_fund_wallet_payment_link, get_payment_link


router = APIRouter(prefix="/api/payment", tags=["Payments/Transations"])


@router.get(
    "/all-transactions",
    response_model=TransactionResponseSchema,
    status_code=status.HTTP_200_OK,
)
async def get_transactions(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    transaction_type: TransactionType = Query(
        None, description="Filter by transaction type"
    ),
    payment_status: PaymentStatus = Query(None, description="Filter by payment status"),
    payment_method: PaymentMethod = Query(None, description="Filter by payment method"),
    start_date: str = Query(None, description="Filter by start date (ISO format)"),
    end_date: str = Query(None, description="Filter by end date (ISO format)"),
    min_amount: float = Query(None, ge=0, description="Filter by minimum amount"),
    max_amount: float = Query(None, ge=0, description="Filter by maximum amount"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all transactions with filtering and pagination.

    Args:
        page: Page number (1-based)
        page_size: Number of items per page (1-100)
        transaction_type: Filter by transaction type
        payment_status: Filter by payment status
        payment_method: Filter by payment method
        start_date: Filter by start date (ISO format: YYYY-MM-DDTHH:MM:SS)
        end_date: Filter by end date (ISO format: YYYY-MM-DDTHH:MM:SS)
        min_amount: Filter by minimum amount
        max_amount: Filter by maximum amount
        db: Database session

    Returns:
        Paginated list of all transactions with metadata
    """
    # Build filters
    filters = None
    if any(
        [
            transaction_type,
            payment_status,
            payment_method,
            start_date,
            end_date,
            min_amount,
            max_amount,
        ]
    ):
        from datetime import datetime
        from decimal import Decimal

        filters = TransactionFilterSchema(
            transaction_type=transaction_type,
            payment_status=payment_status,
            payment_method=payment_method,
            start_date=datetime.fromisoformat(start_date) if start_date else None,
            end_date=datetime.fromisoformat(end_date) if end_date else None,
            min_amount=Decimal(str(min_amount)) if min_amount else None,
            max_amount=Decimal(str(max_amount)) if max_amount else None,
        )

    return await transaction_service.get_transactions(
        db=db,
        page=page,
        page_size=page_size,
        filters=filters,
    )


@router.get(
    "/transactions",
    response_model=list[TransactionSchema],
    status_code=status.HTTP_200_OK,
)
async def get_all_transactions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await transaction_service.get_all_transactions(db=db, skip=skip, limit=limit)


@router.get(
    "/{transaction_id}/transactions",
    response_model=TransactionSchema,
    status_code=status.HTTP_200_OK,
)
async def get_transaction(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await transaction_service.get_transaction(
        db=db, transaction_id=transaction_id
    )


@router.get(
    "/order-payment-callback",
    include_in_schema=False,
    response_class=HTMLResponse,
    status_code=status.HTTP_200_OK,
)
async def order_payment_callback(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    
    return await transaction_service.order_payment_callback(request=request, db=db)


@router.get(
    "/product-payment-callback",
    include_in_schema=False,
    response_class=HTMLResponse,
    status_code=status.HTTP_200_OK,
)
async def product_payment_callback(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    return await transaction_service.product_order_payment_callback(
        request=request, db=db
    )


@router.get(
    "/fund-wallet-callback",
    include_in_schema=False,
    response_class=HTMLResponse,
    status_code=status.HTTP_200_OK,
)
async def fund_wallet_payment_callback(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    return await transaction_service.fund_wallet_callback(request=request, db=db)


@router.post("/withdraw-funds", status_code=status.HTTP_200_OK)
async def withdraw_funds(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WithdrawalShema:
    return await transaction_service.make_withdrawal(current_user=current_user, db=db)


@router.post("/webhook", include_in_schema=False, status_code=status.HTTP_200_OK)
async def process_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    return await transaction_service.handle_payment_webhook(
        request=request,
        db=db,
    )


@router.post("/fund-wallet", status_code=status.HTTP_200_OK)
async def fund_wallet(
    data: TopUpRequestSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TopUpResponseSchema:
    return await transaction_service.top_up_wallet(
        topup_data=data, db=db, current_user=current_user
    )


@router.post("/{order_id}/init-bank-transfer", status_code=status.HTTP_200_OK)
async def init_bank_transfer(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TransferDetailResponseSchema:
    return await transaction_service.initiate_bank_transfer(
        db=db, current_user=current_user, order_id=order_id
    )


@router.post("/{order_id}/pay-with-wallet", status_code=status.HTTP_200_OK)
async def pay_with_wallet(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Pay for an order using the user's wallet.
    """
    return await transaction_service.pay_with_wallet(
        db=db, order_id=order_id, customer=current_user
    )


@router.post(
    "/bank-transfer-callback", include_in_schema=False, status_code=status.HTTP_200_OK
)
async def bank_transfer_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await transaction_service.bank_payment_transfer_callback(
        request=request, db=db
    )


@router.put(
    "/{id}/generate-new-payment-link",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_new_payment_link(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db),
    link_type: GenerateLinkType = GenerateLinkType.ORDER
) -> PaymentLinkSchema:
    """
    Generate a new payment link for a transaction.
    """
    transaction = await db.get(Transaction, id)
    order = await db.get(Order, id)
    tx_ref = uuid.uuid()

    if not transaction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    try:
        if link_type == GenerateLinkType.FUND_WALLET:
            transaction_payment_link = await get_fund_wallet_payment_link(
                id=transaction_id, amount=transaction.amount, db=db
            )

            transaction.payment_link = transaction_payment_link
            await db.commit()

            return PaymentLinkSchema(payment_link=transaction_payment_link)
        else:
            order_payment_link = await get_payment_link(
                tx_ref=tx_ref, amount=order.order.grand_total, db=db
            )

            order.payment_link = transaction_payment_link
            order.tx_ref = tx_ref
            await db.commit()

            return PaymentLinkSchema(payment_link=order_payment_link)
            

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
