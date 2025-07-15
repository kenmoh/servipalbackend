from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.auth.auth import get_current_user
from app.database.database import get_db
from app.models.models import User
from app.schemas.settings_schema import (
    ChargeAndCommissionSchema,
    ChargeAndCommissionUpdateSchema,
    ChargeAndCommissionCreateSchema,
    SettingsResponseSchema,
)
from app.services.settings_service import (
    get_charge_and_commission_settings,
    update_charge_and_commission_settings,
    create_initial_charge_and_commission_settings,
    delete_charge_and_commission_settings,
)

router = APIRouter(prefix="/api/settings", tags=["Settings"])


@router.get(
    "/charge-commission",
    response_model=Optional[ChargeAndCommissionSchema],
    status_code=status.HTTP_200_OK,
    summary="Get charge and commission settings",
    description="Retrieve the current charge and commission settings for transactions.",
)
async def get_charge_commission_settings(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Get the current charge and commission settings.

    - **Requires**: Any authenticated user
    - **Returns**: Current charge and commission settings or None if not configured
    """
    return await get_charge_and_commission_settings(db)


# @router.post(
#     "/charge-commission",
#     response_model=SettingsResponseSchema,
#     status_code=status.HTTP_201_CREATED,
#     summary="Create initial charge and commission settings",
#     description="Create the initial charge and commission settings. Can only be done once.",
# )
# async def create_charge_commission_settings(
#     create_data: ChargeAndCommissionCreateSchema,
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_current_user),
# ):
#     """
#     Create initial charge and commission settings.

#     - **Requires**: Admin or Staff user
#     - **Body**: All charge and commission fields are required
#     - **Returns**: Created settings with success message

#     **Note**: This endpoint can only be used once to create initial settings.
#     Use the PUT endpoint to update existing settings.
#     """
#     return await create_initial_charge_and_commission_settings(
#         db, create_data, current_user
#     )


@router.put(
    "/charge-commission",
    response_model=SettingsResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Update charge and commission settings",
    description="Update existing charge and commission settings. Only provided fields will be updated.",
)
async def update_charge_commission_settings(
    update_data: ChargeAndCommissionUpdateSchema,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update existing charge and commission settings.

    - **Requires**: Admin or Staff user
    - **Body**: Only include fields you want to update (all fields are optional)
    - **Returns**: Updated settings with success message

    **Example requests:**

    Update only VAT:
    ```json
    {
        "value_added_tax": 0.075
    }
    ```

    Update all charges:
    ```json
    {
        "payout_charge_transaction_upto_5000_naira": 25.00,
        "payout_charge_transaction_from_5001_to_50_000_naira": 50.00,
        "payout_charge_transaction_above_50_000_naira": 100.00,
        "value_added_tax": 0.075
    }
    ```
    """
    return await update_charge_and_commission_settings(db, update_data, current_user)


# @router.delete(
#     "/charge-commission",
#     response_model=SettingsResponseSchema,
#     status_code=status.HTTP_200_OK,
#     summary="Delete charge and commission settings",
#     description="Delete all charge and commission settings. Admin only operation.",
# )
# async def delete_charge_commission_settings(
#     db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
# ):
#     """
#     Delete charge and commission settings.

#     - **Requires**: Admin user only
#     - **Returns**: Success message confirming deletion

#     **Warning**: This operation cannot be undone. Use with caution.
#     """
#     return await delete_charge_and_commission_settings(db, current_user)


# Additional utility endpoints


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Settings service health check",
    description="Check if the settings service is working properly.",
)
async def settings_health_check():
    """
    Health check endpoint for the settings service.
    """
    return {
        "status": "healthy",
        "service": "settings",
        "message": "Settings service is operational",
    }


@router.get(
    "/charge-commission/calculate-fee",
    status_code=status.HTTP_200_OK,
    summary="Calculate transaction fee",
    description="Calculate the transaction fee for a given amount based on current settings.",
)
async def calculate_transaction_fee(
    amount: float,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Calculate transaction fee for a given amount.

    - **Requires**: Any authenticated user
    - **Parameters**:
        - amount: The transaction amount to calculate fee for
    - **Returns**: Breakdown of fees including base charge, VAT, and total
    """
    from decimal import Decimal

    settings = await get_charge_and_commission_settings(db)
    if not settings:
        return {
            "error": "No charge and commission settings configured",
            "amount": amount,
            "fees": None,
        }

    amount_decimal = Decimal(str(amount))

    # Determine which charge tier applies
    if amount_decimal <= 5000:
        base_charge = settings.payout_charge_transaction_upto_5000_naira
        tier = "up_to_5000"
    elif amount_decimal <= 50000:
        base_charge = settings.payout_charge_transaction_from_5001_to_50_000_naira
        tier = "5001_to_50000"
    else:
        base_charge = settings.payout_charge_transaction_above_50_000_naira
        tier = "above_50000"

    # Calculate VAT
    vat_amount = base_charge * settings.value_added_tax
    total_fee = base_charge + vat_amount
    net_amount = amount_decimal - total_fee

    return {
        "amount": float(amount_decimal),
        "tier": tier,
        "fees": {
            "base_charge": float(base_charge),
            "vat_rate": float(settings.value_added_tax),
            "vat_amount": float(vat_amount),
            "total_fee": float(total_fee),
            "net_amount": float(net_amount),
        },
        "settings_used": {"id": settings.id, "updated_at": settings.updated_at},
    }
