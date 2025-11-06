from pydantic import BaseModel
from uuid import UUID


class ContactCreate(BaseModel):
    full_name: str
    email: str
    category: str
    subject: str
    message: str

class ContactResponse(ContactCreate):
	id: UUID


class SubscribeCreate(BaseModel):
    email: str


class SubscriberResponse(SubscribeCreate):
    id: UUID