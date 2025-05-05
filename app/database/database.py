from typing import AsyncGenerator
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncAttrs,
)
from sqlalchemy.orm import DeclarativeBase

from app.config.config import settings


SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL
DEBUG = settings.DEBUG

# engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True if DEBUG else False)
# SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
# session = SessionLocal()


engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(AsyncAttrs, DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            session.commit()
        except Exception as e:
            await session.rollback()
            raise e
        finally:
            await session.close()


# def get_db():
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
