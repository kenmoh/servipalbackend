from app.schemas.conctact_schema import ContactCreate, ContactResponse, SubscriberCreate, SubscriberResponse
from app.models.models import Subscribe, Contact
from sqlalchemy.ext.asyncio import AsyncSession


async def create_contact(db: AsyncSession, contact: ContactCreate)-> ContactResponse:

	try:

		new_contact = Contact(
				full_name=contact.full_name,
				email=contact.email,
				subject=contact.subject,
				category=contact.category,
				message=contact.message
			)

		db.add(new_contact)
		await db.commit()
		await db.refresh(new_contact)

		return new_contact

	except Exception as e:
		db.rollback()
		raise e


async def get_contacts(db: AsyncSession)-> list[ContactResponse]:

	try:
		stmt = select(Contact).order_by(created_at)
		result = await db.execute(stmt)
		contacts = result.scalars().all()
	except Exception as e:
		db.rollback()
		raise e

async def subscribe(db: AsyncSession, subscribe: SubscriberCreate)-> SubscriberResponse:
	try:
		new_sub = Subscribe(email=subscribe.email)
		db.add(new_sub)
		await db.commit()
		await db.refresh()

		return new_sub

	except Exception as e:
		db.rollback()
		raise e



async def get_subscribers(db: AsyncSession) -> list[SubscriberResponse]:

	stmt = select(Subscribe).order_by(created_at)
	result = await db.execute(stmt)
	subscribers = result.scalars().all()

	return subscribers