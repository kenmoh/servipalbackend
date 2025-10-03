#!/usr/bin/env python3
"""
Simple test to verify basic functionality without full app dependencies.
"""

import asyncio
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text

# Test database connection
async def test_database_connection():
    """Test that we can connect to the test database."""
    DATABASE_URL = "postgresql+asyncpg://kenmoh:V9ZFjDL4PktG@ep-calm-night-19028536-pooler.us-east-2.aws.neon.tech/servipal-test-db?ssl=require"
    
    engine = create_async_engine(DATABASE_URL, echo=False)
    
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1 as test"))
            row = result.fetchone()
            assert row[0] == 1
            print("✅ Database connection successful!")
            
        # Test creating a session
        async_session = async_sessionmaker(engine, class_=AsyncSession)
        async with async_session() as session:
            result = await session.execute(text("SELECT version()"))
            version = result.fetchone()
            print(f"✅ PostgreSQL version: {version[0][:50]}...")
            
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        raise
    finally:
        await engine.dispose()


async def test_uuid_support():
    """Test that PostgreSQL supports UUIDs."""
    DATABASE_URL = "postgresql+asyncpg://kenmoh:V9ZFjDL4PktG@ep-calm-night-19028536-pooler.us-east-2.aws.neon.tech/servipal-test-db?ssl=require"
    
    engine = create_async_engine(DATABASE_URL, echo=False)
    
    try:
        async with engine.begin() as conn:
            # Test UUID generation
            result = await conn.execute(text("SELECT gen_random_uuid() as uuid_test"))
            row = result.fetchone()
            uuid_value = row[0]
            print(f"✅ UUID generation works: {uuid_value}")
            
            # Test UUID type
            await conn.execute(text("""
                CREATE TEMP TABLE test_uuid (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name TEXT
                )
            """))
            
            await conn.execute(text("""
                INSERT INTO test_uuid (name) VALUES ('test')
            """))
            
            result = await conn.execute(text("SELECT id, name FROM test_uuid"))
            row = result.fetchone()
            print(f"✅ UUID table operations work: {row[0]}, {row[1]}")
            
    except Exception as e:
        print(f"❌ UUID test failed: {e}")
        raise
    finally:
        await engine.dispose()


async def main():
    """Run all tests."""
    print("🧪 Running simple database tests...")
    
    try:
        await test_database_connection()
        await test_uuid_support()
        print("\n🎉 All tests passed! Your database setup is working correctly.")
        print("✅ PostgreSQL connection: OK")
        print("✅ UUID support: OK")
        print("✅ Ready for full test suite")
        
    except Exception as e:
        print(f"\n❌ Tests failed: {e}")
        return False
    
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
