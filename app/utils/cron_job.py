from datetime import datetime, timedelta
from sqlalchemy import insert, select, update
from app.models.models import AuditLog, User
from app.database.database import async_session
from app.utils.logger_config import setup_logger

# import logging

# logger = logging.getLogger(__name__)
logger = setup_logger()


async def suspend_user_with_order_cancel_count_equal_3():
    """
    Suspend users who have cancelled 3 orders
    """
    async with async_session() as session:
        try:
            # Find users with 3 cancellations
            result = await session.execute(
                select(User).where(
                    User.order_cancel_count == 3,
                    User.rider_is_suspended_for_order_cancel == False,
                )
            )
            users = result.scalars().all()

            if not users:
                logger.info("No users found with 3 order cancellations")
                return

            suspension_until = datetime.now() + timedelta(days=3)

            # Update users in bulk
            await session.execute(
                update(User)
                .where(User.id.in_([user.id for user in users]))
                .values(
                    rider_is_suspended_for_order_cancel=True,
                    rider_is_suspension_until=suspension_until,
                )
            )

            await session.commit()
            logger.info(f"Suspended {len(users)} users until {suspension_until}")

            # --- AUDIT LOG ---
            for user in users:
                await session.exeute(
                    insert(AuditLog).values(
                        actor_id=user.id,
                        actor_name=getattr(user, "email", "unknown"),
                        actor_role=str(getattr(user, "user_type", "unknown")),
                        action="auto_suspend_user",
                        resource_type="User",
                        resource_id=user.id,
                        resource_summary=user.email,
                        changes={
                            "rider_is_suspended_for_order_cancel": [False, True],
                            "rider_is_suspension_until": [None, suspension_until],
                        },
                        extra_metadata={"reason": "3 order cancellations (auto)"},
                    )
                )
                await session.commit()

        except Exception as e:
            await session.rollback()
            logger.error(f"Error suspending users: {str(e)}")
            raise


async def reset_user_suspension():
    """
    Reset suspension for users whose suspension period has expired
    """
    async with async_session() as session:
        try:
            now = datetime.now()

            # Find suspended users whose suspension period has expired
            result = await session.execute(
                select(User).where(
                    User.rider_is_suspended_for_order_cancel == True,
                    User.rider_is_suspension_until <= now,
                )
            )
            users = result.scalars().all()

            if not users:
                logger.info("No users found for suspension reset")
                return

            # Update users in bulk
            await session.execute(
                update(User)
                .where(User.id.in_([user.id for user in users]))
                .values(
                    rider_is_suspended_for_order_cancel=False,
                    rider_is_suspension_until=None,
                    order_cancel_count=0,
                )
            )

            await session.commit()
            logger.info(f"Reset suspension for {len(users)} users")

        except Exception as e:
            await session.rollback()
            logger.error(f"Error resetting user suspensions: {str(e)}")
            raise
