# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy import select
# from app.database.database import async_session
# from app.models.models import Order
# from app.utils.utils import verify_transaction_tx_ref



# def poll_for_failed_tranx(db: AsyncSession):
#     # orders = session.query(Order).all()
#     result = await db.execute(select(Order))
#     orders = result.scalars().all()



#     failed_payments = orders


#     for tranx in failed_payments:
#         try:
#              user = None
#             response_data = verify_transaction_tx_ref(tranx.id)
#             if (
#                 response_data
#                 and response_data.get("data", {}).get("status") == "failed"
#             ):
#                 # Check if the transaction is already in the failed_payments table
#                 existing_failed_tranx = (
#                     session.query(FailedPayment)
#                     .filter_by(transaction_id=tranx.id)
#                     .first()
#                 )

#                 if not existing_failed_tranx:
#                     if isinstance(tranx, Transaction):
#                         user = tranx.user
#                     failed_tranx = FailedPayment(
#                         transaction_id=tranx.id,
#                         item_id=tranx.listing_id or tranx.id,
#                         satatus=tranx.status or tranx.item_status,
#                         username=(tranx.buyer_username or tranx.order_owner_username),
#                         item_name=(
#                             tranx.name
#                             or tranx.package_name
#                             or (
#                                 tranx.foods[0].name
#                                 if tranx.foods
#                                 else tranx.laundries[0].name
#                             )
#                         ),
#                         total_cost=tranx.total_cost,
#                         date=tranx.updated_at,
#                     )
#                     session.add(failed_tranx)
#                     session.commit()
#                     session.refresh(failed_tranx)

#                     logging.info(
#                         f"Transaction with ID: {tranx.id} totalling {tranx.total_cost} failed"
#                     )
#                     # SEND PUSH NOTIFICATION
#                     notification_token = user.notification_token
#                     if notification_token:
#                         send_push_message(
#                             notification_token,
#                             message=f"Your transaction with ID: {tranx.id} has failed.",
#                         )

#         except Exception as e:
#             logging.error(f"Error polling for failed transaction {tranx.id}: {str(e)}")