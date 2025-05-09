from datetime import datetime, time
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID
from fastapi import Depends
from pydantic import EmailStr, BaseModel, Field, constr
from app.schemas.status_schema import AccountStatus, TransactionType, UserType


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class PasswordResetRequest(BaseModel):
    """Schema for initiating password reset"""
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Schema for confirming password reset"""
    token: str
    new_password: str


class PasswordReset(BaseModel):
    email: str
    reset_token: str
    new_password: str


class CreateUserSchema(BaseModel):
    email: EmailStr
    phone_number: str
    user_type: UserType
    password: str


class CreateUserResponseSchema(BaseModel):
    id: str
    email: EmailStr
    phone_number: str
    user_type: UserType
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CreateVendorSchema(BaseModel):
    email: EmailStr
    phone_number: str
    company_name: str | None = None
    company_reg_number: str | None = None
    password: str


class CreateVendorReturnSchema(BaseModel):
    id: str
    email: EmailStr
    phone_number: str
    company_name: str | None = None
    company_reg_number: str | None = None
    user_type: UserType
    created_at: datetime


class UserCount(BaseModel):
    regular_users: int
    total_dispatchers: int
    total_riders: int
    restaurant_users: int
    laundry_users: int

    class Config:
        from_attributes = True


class AccountStatusSchema(BaseModel):
    account_status: AccountStatus


class WalletSchema(BaseModel):
    id: str
    user_id: str
    balance: Decimal


class CreateReviewSchema(BaseModel):
    rating: Decimal = Field(ge=1, le=5)
    comment: str
    name: str | None = None
    profile_image: str | None = None
    created_at: datetime | None = None


class RatingSchema(BaseModel):
    average_rating: Decimal | None = None
    number_of_ratings: int | None = None
    reviews: list[CreateReviewSchema]


class UserReviewResponse(BaseModel):
    user_id: str
    total_reviews: int
    average_rating: Decimal
    reviews: list[CreateReviewSchema]


class RestaurantUserResponse(BaseModel):
    id: str
    company_name: str | None = None
    email: str
    phone_number: str
    profile_image: str | None = None
    location: str | None = None
    company_background_image: str | None = None
    opening_hour: time | None = None
    closing_hour: time | None = None
    rating: RatingSchema


class UpdateCompany(BaseModel):
    location: str
    company_reg_number: str | None
    company_name: str
    opening_hour: time
    closing_hour: time
    bank_account_number: str
    bank_name: str
    account_holder_name: str


class UpdateUserProfile(BaseModel):
    location: str
    bank_account_number: str
    bank_name: str
    account_holder_name: str


class UpdateUserProfileResponse(UpdateUserProfile):
    user_id: str


class UpdateCompanyReturnSchema(UpdateCompany):
    user_id: str


class RiderResponse(BaseModel):
    profile_image: str | None = None
    plate_number: str

    class Config:
        from_attributes = True


class DispatchRiderResponseSchema(BaseModel):
    id: str
    full_name: str
    email: str
    phone_number: str
    profile: RiderResponse


class FavouriteResponseSchema(BaseModel):
    company_name: str
    company_background_image: str


class UserResponseSchema(BaseModel):
    id: str
    dispatch_id: str | None
    full_name: str | None
    email: EmailStr
    phone_number: str
    user_type: UserType
    notification_token: str | None
    is_suspended: bool
    is_verified: bool
    account_status: AccountStatus
    confirm_email: str | None
    confirm_phone_number: str | None
    wallet: WalletSchema | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    id: str
    dispatch_id: str | None = None
    vendor: str | None = None
    full_name: str | None = None
    email: EmailStr | None = None
    phone_number: str | None = None
    photo_url: str | None = None
    is_suspended: bool = False
    user_type: UserType
    wallet: WalletSchema | None = None
    account_status: AccountStatus
    company_name: str | None = None
    vendor_company_name: str | None = None
    company_reg_number: str | None = None

    class Config:
        from_attributes = True


class Notification(BaseModel):
    notification_token: str

    class Config:
        from_attributes = True


class ConfirmAccountSchema(BaseModel):
    email_code: str
    phone_code: str


class UpdateVendorSchema(BaseModel):
    full_name: str
    location: str


class UpdateDispatchSchema(BaseModel):
    company_reg_number: str | None
    location: str
    bank_name: str
    account_holder_name: str
    bank_account_number: str

    class Config:
        from_attributes = True


class UpdateUserSchema(BaseModel):
    bank_name: str
    account_holder_name: str
    bank_account_number: str
    opening_hour: time
    closing_hour: time

    class Config:
        from_attributes = True


class RiderSchema(BaseModel):
    full_name: str
    email: EmailStr
    phone_number: str
    plate_number: str
    password: str


class RiderResponseSchema(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    phone_number: str
    plate_number: str = Field(exclude=True)
    confirm_email: str | None = Field(exclude=True)
    confirm_phone: str | None = Field(exclude=True)
    user_type: UserType = Field(exclude=True)
    dispatch_id: str
    created_at: datetime
    updated_at: datetime


class ResetPasswordEmailSchema(BaseModel):
    email: EmailStr


class ResetPasswordSchema(BaseModel):
    new_password: str


class ChangePasswordSchema(BaseModel):
    old_password: str
    new_password: str


class ResetPasswordString(BaseModel):
    url_string: str
    email: str

    class Config:
        from_attribute = True


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_type: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserBase(BaseModel):
    email: EmailStr
    user_type: UserType


class ProfileSchema(BaseModel):
    phone_number: str | None = None
    bike_number: str | None = None
    bank_account_number: str | None = None
    bank_name: str | None = None
    full_name: str | None = None
    business_name: str | None = None
    business_address: str | None = None
    business_registration_number: str | None = None
    closing_hours: datetime | None = None
    opening_hours: datetime | None = None


class TransactionSchema(BaseModel):
    id: UUID
    wallet_id: UUID
    amount: Decimal
    transaction_type: TransactionType
    created_at: datetime


class WalletSchema(BaseModel):
    id: UUID
    balance: Decimal
    escrow_balance: Decimal
    transactions: list[TransactionSchema] = []


class UserResponse(UserBase):
    id: UUID
    profile: ProfileSchema | None
    wallet: WalletSchema | None


class UserProfileResponse(UserBase):
    id: UUID
    profile: ProfileSchema | None


class WalletRespose(BaseModel):
    id: UUID
    balance: Decimal
    escrow_balance: Decimal


class UserCreate(UserBase):
    password: str


class RiderBase(BaseModel):
    email: EmailStr
    password: str


class PasswordResetForm(BaseModel):
    new_password: str


class RiderCreate(RiderBase):
    phone_number: str
    bike_number: str
    full_name: str


class SessionResponse(BaseModel):
    id: UUID
    device_info: str
    ip_address: str
    last_active: datetime
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AdminSessionResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str = Field(..., alias="user.email")
    device_info: str
    ip_address: str
    last_active: datetime
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class VerificationSchema(BaseModel):
    email_code: str = Field(..., min_length=6, max_length=6)
    phone_code: str = Field(..., min_length=6, max_length=6)
