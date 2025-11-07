#!/usr/bin/env python3
"""
Script to reset the test database with the current schema
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.models.models import Base
from app.config.config import settings

async def reset_test_db():
    """Reset the test database with current schema"""
    engine = create_async_engine(
        settings.TEST_DATABASE_URL,
        echo=True
    )
    
    async with engine.begin() as conn:
        # Drop all tables
        await conn.execute(text("DROP SCHEMA public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO postgres;"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
        
        # Create all tables with current schema
        await conn.run_sync(Base.metadata.create_all)
        
    await engine.dispose()
    print("Test database reset successfully!")

if __name__ == "__main__":
    asyncio.run(reset_test_db())
