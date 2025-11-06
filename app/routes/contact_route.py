from app.services import contact_service
from app.schemas.contact_schema import ContactCreate, ContactResponse, SubscribeCreate, SubscribeResponse
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter

router = APIRouter(prefix='/api/', tags=['Contacts, Subscribers'])

@router.get('/contact')
async def get_contacts(db: AsyncSession)-> list[ContactResponse]:

	return await contact_service.get_contacts(db)

@router.get('/subscribers')
async def get_subscribers(db: AsyncSession)-> list[SubscribeResponse]:

	return await contact_service.get_subscribers(db)

@router.post('/contact')
async def create_contact(db: AsyncSession, contact: ContactResponse)-> ContactResponse:

	return await contact_service.create_contact(db, contact)

@router.post('/subscribers')
async def subscribe(db: AsyncSession, subscribe: SubscriberCreate)-> SubscribeResponse:

	return await contact_service.subscribe(db, subscribe)