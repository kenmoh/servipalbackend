import os
from pathlib import Path
from fastapi_mail import ConnectionConfig
from pydantic import EmailStr
from pydantic_settings import BaseSettings
from dotenv import load_dotenv
import redis

load_dotenv()


class Settings(BaseSettings):
    # Application settings
    APP_NAME: str = "ServiPal"
    DEBUG: bool = os.getenv("DEBUG", False) == True

    # Database settings
    DATABASE_URL: str = os.getenv("DATABASE_URL")

    # FLUTTERWAVE
    # FLUTTERWAVE
    FLW_PUBLIC_KEY: str = os.getenv("FLW_PUBLIC_KEY")
    FLW_SECRET_KEY: str = os.getenv("FLW_SECRET_KEY")
    FLW_SECRET_HASH: str = os.getenv("FLW_SECRET_HASH")

    # JWT settings
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Email
    # Email Settings
    MAIL_USERNAME: str
    MAIL_PASSWORD: str
    MAIL_FROM: str
    MAIL_FROM_NAME: str
    MAIL_PORT: int
    MAIL_SERVER: str
    MAIL_SSL_TLS: bool = True
    MAIL_STARTTLS: bool = False
    USE_CREDENTIALS: bool = True
    EMAIL_TEMPLATES_DIR: str = Path(
        __file__).parent.parent / "templates" / "email"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_EX: int = 3600
    REDIS_PASSWORD: str | None = None  # Set this in production


settings = Settings()


redis_url = "redis://localhost"

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD,
    decode_responses=True,
)


email_conf = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=EmailStr(settings.MAIL_FROM),
    MAIL_FROM_NAME=settings.MAIL_FROM_NAME,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    USE_CREDENTIALS=settings.USE_CREDENTIALS,
    TEMPLATE_FOLDER=settings.EMAIL_TEMPLATES_DIR
)
