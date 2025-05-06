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
    FLW_PUBLIC_KEY: str = os.getenv("FLW_PUBLIC_KEY")
    FLW_SECRET_KEY: str = os.getenv("FLW_SECRET_KEY")
    FLW_SECRET_HASH: str = os.getenv("FLW_SECRET_HASH")

    # JWT settings
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # AWS
    AWS_SECRET_KEY: str = os.getenv("AWSSecretKey")
    AWS_ACCESS_KEY_ID: str = os.getenv("AWSAccessKeyId")
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME")

    # Email Settings
    MAIL_USERNAME: str = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD: str = os.getenv("MAIL_PASSWORD")
    MAIL_FROM: EmailStr = os.getenv("MAIL_FROM")
    MAIL_FROM_NAME: str = os.getenv("MAIL_FROM_NAME")
    MAIL_PORT: int = os.getenv("MAIL_PORT")
    MAIL_SERVER: str = os.getenv("MAIL_SERVER")
    MAIL_SSL_TLS: bool = os.getenv("MAIL_SSL_TLS")
    MAIL_STARTTLS: bool = os.getenv("MAIL_STARTTLS")
    USE_CREDENTIALS: bool = os.getenv("USE_CREDENTIALS")
    EMAIL_TEMPLATES_DIR: str = str(
        Path(__file__).parent.parent / "templates" / "email")

    # Database connection settings
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_MAX_RETRIES: int = 3
    DB_RETRY_DELAY: int = 1

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
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_FROM_NAME=settings.MAIL_FROM_NAME,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    USE_CREDENTIALS=settings.USE_CREDENTIALS,
    TEMPLATE_FOLDER=settings.EMAIL_TEMPLATES_DIR
)
