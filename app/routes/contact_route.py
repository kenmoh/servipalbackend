from app.schemas.contact_schema import ContactCreate, ContactResponse, SubscribeCreate, SubscriberResponse
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, status
from app.services import contact_service
from app.database.database import get_db


router = APIRouter(prefix='/api', tags=['Contacts, Subscribers'])

@router.get('/contact', status_code=status.HTTP_200_OK)
async def get_contacts(db: AsyncSession = Depends(get_db))-> list[ContactResponse]:

	return await contact_service.get_contacts(db)

@router.get('/subscribers', status_code=status.HTTP_200_OK)
async def get_subscribers(db: AsyncSession = Depends(get_db))-> list[SubscriberResponse]:

	return await contact_service.get_subscribers(db)

@router.post('/contact', status_code=status.HTTP_201_CREATED)
async def create_contact(contact: ContactCreate, db: AsyncSession = Depends(get_db))-> ContactResponse:

	return await contact_service.create_contact(db, contact)

@router.post('/subscribers', status_code=status.HTTP_201_CREATED)
async def subscribe(subscribe: SubscribeCreate, db: AsyncSession = Depends(get_db))-> SubscriberResponse:

	return await contact_service.subscribe(db, subscribe)