from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from typing import AsyncGenerator
from sqlalchemy.pool import NullPool

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

DEBUG = settings.DEBUG


# def create_test_engine():
#     return create_async_engine(settings.TEST_DATABASE_URL, future=True)


def create_test_session(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def create_test_engine():
    """Create test database engine with prepared statements disabled."""
    return create_async_engine(
        settings.TEST_DATABASE_URL,  # or your test DB URL
        poolclass=NullPool,  # Don't pool connections in tests
        echo=False,
        connect_args={
            "prepared_statement_cache_size": 0,  # CRITICAL: Disable prepared statement cache
            "statement_cache_size": 0,  # Also disable statement cache
        },
    )


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


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
