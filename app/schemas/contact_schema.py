from pydantic import BaseModel
from uuid import UUID


class ContanctCreate(BaseModel):
    full_name: str
    email: str
    category: str
    subject: str
    message: str

class ContactResponse(ContanctCreate):
	id: UUID


class SubscribeCreate(BaseModel):
    email: str


class SubscriberResponse(SubscribeCreate):
    id: UUID