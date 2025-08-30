from datetime import datetime, time
from decimal import Decimal
from uuid import UUID
from pydantic import EmailStr, BaseModel, Field, ConfigDict
from app.schemas.status_schema import (
    AccountStatus,
    TransactionType,
    UserType,
    PaymentStatus,
)


class UserCoords(BaseModel):
    latitude: float
    longitude: float

class AccountDetails(BaseModel):
    account_number: str
    account_bank: str


class AccountDetailResponse(BaseModel):
    account_number: str
    account_name: str


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
    confirm_new_password: str


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


class TransactionSchema(BaseModel):
    id: UUID
    wallet_id: UUID
    amount: Decimal
    payment_status: PaymentStatus
    from_user: str | None = None
    to_user: str | None = None
    transaction_direction: str | None = None
    payment_link: str | None = None
    transaction_type: TransactionType
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class WalletUserData(BaseModel):
    full_name: str | None = None
    business_name: str | None = None
    phone_number: str


class WalletSchema(BaseModel):
    id: UUID
    balance: Decimal
    escrow_balance: Decimal
    profile: WalletUserData | None = None
    transactions: list[TransactionSchema] = []

    model_config = ConfigDict(from_attributes=True)


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


class CreateReviewSchema(BaseModel):
    rating: Decimal = Field(ge=1, le=5)
    comment: str
    name: str | None = None
    profile_image: str | None = None
    created_at: datetime | None = None


class RatingSchema(BaseModel):
    average_rating: Decimal | None = None
    number_of_reviews: int | None = None
    # reviews: list[CreateReviewSchema] = []


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


class RiderProfileSchema(BaseModel):
    profile_image_url: str | None = None
    full_name: str
    email: str
    phone_number: str
    business_address: str
    business_name: str
    bike_number: str


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


class UpdateStaffSchema(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None

    class Config:
        from_attributes = True


class RiderBase(BaseModel):
    password: str
    email: EmailStr


class RiderSchema(RiderBase):
    full_name: str
    phone_number: str
    plate_number: str


class UpdateRider(BaseModel):
    full_name: str
    phone_number: str
    bike_number: str


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
    email: str | None = None
    chat_token: str | None = None
    account_status: AccountStatus


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserBase(BaseModel):
    email: EmailStr
    user_type: UserType

    model_config = ConfigDict(from_attributes=True)


class ProfileSchema(BaseModel):
    phone_number: str
    bike_number: str | None = None
    bank_account_number: str | None = None
    bank_name: str | None = None
    full_name: str | None = None
    state: str | None = None
    business_name: str | None = None
    store_name: str | None = None
    business_address: str | None = None
    business_registration_number: str | None = None
    closing_hours: time | None = None
    opening_hours: time | None = None
    account_holder_name: str | None = None
    profile_image_url: str | None = None
    backdrop_image_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class UserResponse(UserBase):
    id: UUID
    profile: ProfileSchema | None
    wallet: WalletSchema | None


class UserProfileResponse(UserBase):
    id: UUID
    account_status: AccountStatus
    is_blocked: bool
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


class StaffCreate(BaseModel):
    email: EmailStr
    phone_number: str
    full_name: str
    password: str


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
    email_code: str
    phone_code: str


class VendorUserResponse(BaseModel):
    id: str
    company_name: str | None = None
    email: str
    phone_number: str
    profile_image: str | None = None
    location: str | None = None
    backdrop_image_url: str | None = None
    opening_hour: time | None = None
    closing_hour: time | None = None
    rating: RatingSchema


class ProfileImageResponseSchema(BaseModel):
    profile_image_url: str | None = None
    backdrop_image_url: str | None = None
