from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, EmailStr


class UserBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    email: EmailStr
    user_type: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "email": "user@example.com",
                    "password": "strongpass123",
                    "user_type": "vendor",
                }
            ]
        }
    }


class ReviewSchema(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = None


class RiderStatsSchema(BaseModel):
    total_deliveries: int
    pending_deliveries: int
    completed_deliveries: int


class DispatchRiderSchema(BaseModel):
    id: UUID
    full_name: str | None
    email: str
    bike_number: str | None
    phone_number: str
    profile_image_url: str | None = None
    stats: RiderStatsSchema
    created_at: datetime

    class Config:
        from_attributes = True


class PaymentLinkSchema(BaseModel):
    payment_link: str