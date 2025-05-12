from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.database import async_session, get_db
from app.routes import (
    auth_routes,
    user_routes,
    payment_routes,
    item_routes,
    order_routes,
    product_routes,
    marketplace_routes,
)
from app.utils.utils import get_all_banks
from app.config.config import redis_client


# scheduler = BlockingScheduler()

templates = Jinja2Templates(directory="templates")


@asynccontextmanager
async def lifespan(application: FastAPI):
    # db = async_session()
    try:
        print("Starting up...")
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
            await db.execute(
                text(
                    "CREATE SEQUENCE IF NOT EXISTS order_number_seq START WITH 1000 INCREMENT BY 1"
                )
            )

        print("Database connection successful.")
        # Check Redis connection
        redis_client.ping()
        print("Redis connection successful.")

        yield
        # scheduler.start()
        # scheduler.add_job(poll_for_failed_tranx, "interval", minutes=30)
    finally:
        print("Shutting down...")
        await db.close()


app = FastAPI(
    title="ServiPal",
    lifespan=lifespan,
    docs_url="/",
    debug=True,
    summary="Item delivery, food ordering, P2P and laundry services application.",
    contact={
        "name": "ServiPal",
        "url": "https://servipal.com",
        "email": "kenneth.aremoh@gmail.com",
    },
)

origins = ["http://localhost:3000", "https://servipal-admin.vercel.app"]

# noinspection PyTypeChecker
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "HEAD", "PUT", "PATCH", "OPTIONS", "DELETE"],
    allow_headers=[
        "Access-Control-Allow-Headers",
        "Content-Type",
        "Authorization",
        "Access-Control-Allow-Origin",
        "Set-Cookie",
    ],
    expose_headers=["Set-Cookie"],
)
templates = Jinja2Templates(directory="templates")

# logfire.configure(project_name="QuickPickup", service_name="QuickPickup")
# logfire.debug("App Debug mode on")
# logfire.instrument_fastapi(app=app)

# SQLAlchemyInstrumentor().instrument(engine=engine)


@app.get("/api/db", tags=["Health Status"])
async def check_db_health(db: AsyncSession = Depends(get_db)):
    """Check database connectivity"""
    try:
        # Simple query to check connection
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": "disconnected", "error": str(e)}


@app.get("/api/health", tags=["Health Status"])
def api_health_check() -> dict:
    """Check the status of the API"""
    return {"status": "OK", "message": "API up and running"}


@app.get("/api/list-of-banks", response_model=list[str], tags=["Get Banks"])
async def get_banks() -> list:
    """Get list of all supported bank(Nigeria)"""
    return await get_all_banks()


app.include_router(auth_routes.router)
app.include_router(auth_routes.router)
app.include_router(user_routes.router)
app.include_router(item_routes.router)
app.include_router(order_routes.router)
app.include_router(payment_routes.router)
app.include_router(product_routes.router)
app.include_router(marketplace_routes.router)
