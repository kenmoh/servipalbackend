from contextlib import asynccontextmanager
import logging
import asyncio
from functools import partial
import logfire
# from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor


from fastapi import Depends, FastAPI
from fastapi_mcp import FastApiMCP
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.database.database import async_session, get_db
from app.routes import (
    auth_routes,
    user_routes,
    payment_routes,
    item_routes,
    order_routes,
    product_routes,
    marketplace_routes,
    review_routes,
    notification_routes,
)

from app.utils.cron_job import (
    reset_user_suspension,
    suspend_user_with_order_cancel_count_equal_3,
)
from app.schemas.status_schema import BankSchema
from app.utils.logger_config import setup_logger
from app.utils.utils import get_all_banks, resolve_account_details
from app.config.config import redis_client
from app.database.database import engine
from app.schemas.user_schemas import AccountDetails, AccountDetailResponse


logger = setup_logger()

scheduler = BackgroundScheduler()
trigger = IntervalTrigger(hours=8)


async def logged_reset_user_suspension():
    logger.info("Starting user suspension reset job...")
    try:
        await reset_user_suspension()
        logger.info("User suspension reset job completed successfully")
    except Exception as e:
        logger.error(f"Error in reset_user_suspension: {str(e)}")


async def logged_suspend_users():
    logger.info("Starting user suspension check job...")
    try:
        await suspend_user_with_order_cancel_count_equal_3()
        logger.info("User suspension check job completed successfully")
    except Exception as e:
        logger.error(f"Error in suspend_user_with_order_cancel_count_equal_3: {str(e)}")


def run_async(loop, coro):
    """Run coroutine in the given event loop"""
    asyncio.run_coroutine_threadsafe(coro, loop)


# Get event loop for async operations
loop = asyncio.get_event_loop()


# scheduler.add_job(reset_user_suspension, trigger=trigger)
# scheduler.add_job(
#     suspend_user_with_order_cancel_count_equal_3, trigger=trigger)

scheduler.add_job(
    partial(run_async, loop, reset_user_suspension()),
    trigger=trigger,
    id="reset_suspension",
)

scheduler.add_job(
    partial(run_async, loop, suspend_user_with_order_cancel_count_equal_3()),
    trigger=trigger,
    id="suspend_users",
)

scheduler.start()

# templates = Jinja2Templates(directory="templates")


# @asynccontextmanager
# async def lifespan(application: FastAPI):
#     try:
#         print("Starting up...")
#         async with async_session() as db:
#             await db.execute(text("SELECT 1"))
#             await db.execute(
#                 text(
#                     "CREATE SEQUENCE IF NOT EXISTS order_number_seq START WITH 1000 INCREMENT BY 1"
#                 )
#             )
#         print("Database connection successful.")
#         # Check Redis connection
#         redis_client.ping()
#         print("Redis connection successful.")

#         yield
#         print('Scheduler started...')
#         scheduler.shutdown()

#     finally:
#         print("Shutting down...")
#         await db.close()


@asynccontextmanager
async def lifespan(application: FastAPI):
    try:
        print("Starting up...")
        logger.info("Initializing application...")

        async with async_session() as db:
            await db.execute(text("SELECT 1"))
            await db.execute(
                text(
                    "CREATE SEQUENCE IF NOT EXISTS order_number_seq START WITH 1000 INCREMENT BY 1"
                )
            )
        logger.info("Database connection successful")

        # Check Redis connection
        redis_client.ping()
        logger.info("Redis connection successful")

        # Log scheduler status
        logger.info(f"Scheduler running: {scheduler.running}")
        logger.info(f"Scheduled jobs: {scheduler.get_jobs()}")

        yield

        logger.info("Shutting down scheduler...")
        scheduler.shutdown()
        logger.info("Scheduler shutdown complete")

    finally:
        logger.info("Cleaning up resources...")
        await db.close()
        logger.info("Cleanup complete")


app = FastAPI(
    title="ServiPal",
    lifespan=lifespan,
    docs_url="/",
    debug=True,
    summary="Item delivery, food ordering, P2P and laundry services application.",
    contact={
        "name": "ServiPal",
        "url": "https://servipal.com",
        "email": "servipal@servi-pal.com",
    },
)

logfire.configure(service_name="ServiPal")
logfire.debug("App Debug mode on")
logfire.instrument_fastapi(app=app)
logfire.instrument_sqlalchemy(engine=engine)

origins = ["http://localhost:3000", "https://servipal-admin.vercel.app"]

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


@app.get("/api/list-of-banks", response_model=list[BankSchema], tags=["Get Banks"])
async def get_banks():
    """Get list of all supported bank(Nigeria)"""

    return await get_all_banks()


@app.post("/api/resolve-account-name", tags=["Account Name"])
async def resolve_account_name(data: AccountDetails) -> AccountDetailResponse:
    """Verify account name"""

    return await resolve_account_details(data)


app.include_router(auth_routes.router)
app.include_router(auth_routes.router)
app.include_router(user_routes.router)
app.include_router(review_routes.router)
app.include_router(review_routes.report)
app.include_router(item_routes.router)
app.include_router(order_routes.router)
app.include_router(payment_routes.router)
app.include_router(product_routes.router)
app.include_router(marketplace_routes.router)
app.include_router(notification_routes.router)



mcp = FastApiMCP(app,  include_tags=["Notifications", "Reports"])

mcp.mount()