from functools import wraps
from typing import Callable, AsyncGenerator
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError
from fastapi import HTTPException, status
from app.config.config import settings


def with_db_retry(max_retries: int = 3, delay: int = 1):
    """
    Decorator for database operations with retry mechanism
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> AsyncGenerator[AsyncSession, None]:
            last_error = None
            for attempt in range(max_retries):
                try:
                    async for session in func(*args, **kwargs):
                        try:
                            yield session
                        except Exception as e:
                            await session.rollback()
                            raise e
                        finally:
                            await session.close()
                    return
                except DBAPIError as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        await asyncio.sleep(delay * (2 ** attempt))
                    continue

            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Database connection failed after {max_retries} attempts"
            ) from last_error
        return wrapper
    return decorator
