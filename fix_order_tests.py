#!/usr/bin/env python3
"""
Temporary fix for order service tests by adding missing columns to the test database
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.config.config import settings

async def add_missing_columns():
    """Add missing columns to the test database"""
    engine = create_async_engine(
        settings.TEST_DATABASE_URL,
        echo=True
    )
    
    async with engine.begin() as conn:
        # Add missing columns to profile table
        try:
            await conn.execute(text("ALTER TABLE profile ADD COLUMN total_distance_travelled NUMERIC DEFAULT 0.0;"))
            print("Added total_distance_travelled to profile table")
        except Exception as e:
            print(f"Column total_distance_travelled might already exist: {e}")
        
        # Add missing columns to users table
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN is_online BOOLEAN DEFAULT TRUE;"))
            print("Added is_online to users table")
        except Exception as e:
            print(f"Column is_online might already exist: {e}")
            
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN has_delivery BOOLEAN DEFAULT FALSE;"))
            print("Added has_delivery to users table")
        except Exception as e:
            print(f"Column has_delivery might already exist: {e}")
            
        # Note: We skip location_coordinates as it requires PostGIS extension
        
    await engine.dispose()
    print("Database schema updated successfully!")

if __name__ == "__main__":
    asyncio.run(add_missing_columns())
