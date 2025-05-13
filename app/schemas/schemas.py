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
