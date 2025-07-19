from datetime import datetime
import json
from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AuditLog, ChargeAndCommission, User
from app.schemas.settings_schema import (
    ChargeAndCommissionSchema,
    ChargeAndCommissionUpdateSchema,
    SettingsResponseSchema,
)
from app.schemas.status_schema import UserType
from app.utils.logger_config import setup_logger
from app.config.config import redis_client


logger = setup_logger()


async def get_charge_and_commission_settings(
    db: AsyncSession
) -> ChargeAndCommissionSchema:
    """
    Retrieve the current charge and commission settings.

    Args:
        db: Database session

    Returns:
        ChargeAndCommissionSchema or None if no settings exist
    """
    try:
        # Check cache first
        cache_key = "charge_commission_settings"
        cached_settings = redis_client.get(cache_key)

        if cached_settings:
            settings_data = json.loads(cached_settings)
            return ChargeAndCommissionSchema(**settings_data)

        # Query database
        stmt = (
            select(ChargeAndCommission)
            .order_by(ChargeAndCommission.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        settings = result.scalar_one_or_none()

        if settings:
            settings_dict = {
                "id": settings.id,
                "payment_gate_way_fee": settings.payment_gate_way_fee,
                "value_added_tax": settings.value_added_tax,
                "payout_charge_transaction_upto_5000_naira": settings.payout_charge_transaction_upto_5000_naira,
                "payout_charge_transaction_from_5001_to_50_000_naira": settings.payout_charge_transaction_from_5001_to_50_000_naira,
                "payout_charge_transaction_above_50_000_naira": settings.payout_charge_transaction_above_50_000_naira,
                "stamp_duty": settings.stamp_duty,
                "base_delivery_fee": settings.base_delivery_fee,
                "delivery_fee_per_km": settings.delivery_fee_per_km,
                "delivery_commission_percentage": settings.delivery_commission_percentage,
                "food_laundry_commission_percentage": settings.food_laundry_commission_percentage,
                "product_commission_percentage": settings.product_commission_percentage,
                "created_at": settings.created_at.isoformat(),
                "updated_at": settings.updated_at.isoformat(),
            }

            # Cache for 1 hour
            redis_client.setex(cache_key, 3600, json.dumps(settings_dict, default=str))

            return ChargeAndCommissionSchema(**settings_dict)

        return None

    except Exception as e:
        logger.error(f"Error retrieving charge and commission settings: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve settings",
        )


async def update_charge_and_commission_settings(
    db: AsyncSession, update_data: ChargeAndCommissionUpdateSchema, current_user: User
) -> SettingsResponseSchema:
    """
    Update charge and commission settings.

    Args:
        db: Database session
        update_data: Updated settings data
        current_user: Current authenticated user

    Returns:
        SettingsResponseSchema with updated settings
    """
    # Check if user has permission to update settings
    if current_user.user_type not in [UserType.ADMIN, UserType.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin and superadmin users can update charge and commission settings",
        )

    try:
        # Get current settings
        current_settings = await get_charge_and_commission_settings(db)

        if not current_settings:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No charge and commission settings found. Please create initial settings first.",
            )

        # Prepare update data (only include fields that were provided)
        update_dict = {}
        update_dict["updated_at"] = datetime.now()

        if update_data.payment_gate_way_fee is not None:
            update_dict["payment_gate_way_fee"] = update_data.payment_gate_way_fee

        if update_data.value_added_tax is not None:
            update_dict["value_added_tax"] = update_data.value_added_tax

        if update_data.payout_charge_transaction_upto_5000_naira is not None:
            update_dict[
                "payout_charge_transaction_upto_5000_naira"
            ] = update_data.payout_charge_transaction_upto_5000_naira

        if update_data.payout_charge_transaction_from_5001_to_50_000_naira is not None:
            update_dict[
                "payout_charge_transaction_from_5001_to_50_000_naira"
            ] = update_data.payout_charge_transaction_from_5001_to_50_000_naira

        if update_data.payout_charge_transaction_above_50_000_naira is not None:
            update_dict[
                "payout_charge_transaction_above_50_000_naira"
            ] = update_data.payout_charge_transaction_above_50_000_naira

        if update_data.stamp_duty is not None:
            update_dict["stamp_duty"] = update_data.stamp_duty

        if update_data.base_delivery_fee is not None:
            update_dict["base_delivery_fee"] = update_data.base_delivery_fee

        if update_data.delivery_fee_per_km is not None:
            update_dict["delivery_fee_per_km"] = update_data.delivery_fee_per_km

        if update_data.delivery_commission_percentage is not None:
            update_dict[
                "delivery_commission_percentage"
            ] = update_data.delivery_commission_percentage

        if update_data.food_laundry_commission_percentage is not None:
            update_dict[
                "food_laundry_commission_percentage"
            ] = update_data.food_laundry_commission_percentage

        if update_data.product_commission_percentage is not None:
            update_dict[
                "product_commission_percentage"
            ] = update_data.product_commission_percentage

        # Update the settings
        stmt = (
            update(ChargeAndCommission)
            .where(ChargeAndCommission.id == current_settings.id)
            .values(**update_dict)
        )
        # --- AUDIT LOG ---
        audit = AuditLog(
            actor_id=current_user.id,
            actor_name=getattr(current_user, "email", "unknown"),
            actor_role=str(current_user.user_type),
            action="update_charge_and_commission_settings",
            resource_type="ChargeAndCommission",
            resource_id=current_settings.id,
            resource_summary="Charge and Commission Settings",
            changes=update_dict,
            extra_metadata=None,
        )
        db.add(audit)
        await db.commit()

        # Clear cache
        redis_client.delete("charge_commission_settings")

        # Get updated settings
        updated_settings = await get_charge_and_commission_settings(db)

        logger.info(f"Charge and commission settings updated by user {current_user.id}")

        return SettingsResponseSchema(
            success=True,
            message="Charge and commission settings updated successfully",
            data=updated_settings,
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating charge and commission settings: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update settings",
        )


# async def create_initial_charge_and_commission_settings(
#     db: AsyncSession, create_data: ChargeAndCommissionCreateSchema, current_user: User
# ) -> SettingsResponseSchema:
#     """
#     Create initial charge and commission settings.

#     Args:
#         db: Database session
#         create_data: Initial settings data
#         current_user: Current authenticated user

#     Returns:
#         SettingsResponseSchema with created settings
#     """
#     # Check if user has permission to create settings
#     if current_user.user_type not in [UserType.ADMIN, UserType.SUPER_ADMIN]:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Only admin and staff users can create charge and commission settings",
#         )

#     try:
#         # Check if settings already exist
#         existing_settings = await get_charge_and_commission_settings(db)
#         if existing_settings:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="Charge and commission settings already exist. Use update endpoint instead.",
#             )

#         # Create new settings
#         new_settings = ChargeAndCommission(
#             payment_gate_way_fee=create_data.payment_gate_way_fee,
#             value_added_tax=create_data.value_added_tax,
#             payout_charge_transaction_upto_5000_naira=create_data.payout_charge_transaction_upto_5000_naira,
#             payout_charge_transaction_from_5001_to_50_000_naira=create_data.payout_charge_transaction_from_5001_to_50_000_naira,
#             payout_charge_transaction_above_50_000_naira=create_data.payout_charge_transaction_above_50_000_naira,
#             stamp_duty=create_data.stamp_duty,
#             base_delivery_fee=create_data.base_delivery_fee,
#             delivery_fee_per_km=create_data.delivery_fee_per_km,
#             delivery_commission_percentage=create_data.delivery_commission_percentage,
#             food_laundry_commission_percentage=create_data.food_laundry_commission_percentage,
#             product_commission_percentage=create_data.product_commission_percentage,
#             created_at=datetime.now(),
#             updated_at=datetime.now(),
#         )

#         db.add(new_settings)
#         await db.commit()
#         await db.refresh(new_settings)

#         # Clear cache
#         redis_client.delete("charge_commission_settings")

#         # Get created settings
#         created_settings = await get_charge_and_commission_settings(db)

#         logger.info(
#             f"Initial charge and commission settings created by user {current_user.id}"
#         )

#         return SettingsResponseSchema(
#             success=True,
#             message="Initial charge and commission settings created successfully",
#             data=created_settings,
#         )

#     except HTTPException:
#         await db.rollback()
#         raise
#     except Exception as e:
#         await db.rollback()
#         logger.error(f"Error creating initial charge and commission settings: {str(e)}")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Failed to create initial settings",
#         )
