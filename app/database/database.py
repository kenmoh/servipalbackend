from contextlib import asynccontextmanager
from fastapi import HTTPException, status
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
from typing import AsyncGenerator
import os

from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncAttrs,
)
from sqlalchemy.orm import DeclarativeBase

from app.config.config import settings
from app.utils.middleware import with_db_retry

from urllib.parse import urlparse

# Test database URL
TEST_DATABASE_URL = settings.TEST_DATABASE_URL

SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL
DEBUG = settings.DEBUG

# engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True if DEBUG else False)
# SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
# session = SessionLocal()

# Parse and clean test database URL
test_db_url = settings.TEST_DATABASE_URL
parsed_url = urlparse(test_db_url)
cleaned_test_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

# Create test engine
test_engine = create_async_engine(
    cleaned_test_url,
    echo=True,
    future=True
)
TestingSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False
)


engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG,
                             pool_size=settings.DB_POOL_SIZE,
                             max_overflow=settings.DB_MAX_OVERFLOW,
                             pool_timeout=settings.DB_POOL_TIMEOUT,
                             pool_recycle=settings.DB_POOL_RECYCLE,
                             pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(AsyncAttrs, DeclarativeBase):
    pass


@with_db_retry()
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context():
    """Context manager for database sessions"""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

# async def get_db() -> AsyncGenerator[AsyncSession, None]:
#     async with async_session() as session:
#         try:
#             yield session
#             session.commit()
#         except Exception as e:
#             await session.rollback()
#             raise e
#         finally:
#             await session.close()


# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
