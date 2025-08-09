import os
from app.config.config import settings
from app.utils import limiter

# Set the timezone for the application process. This is crucial for libraries
os.environ["TZ"] = settings.TZ

from contextlib import asynccontextmanager
import asyncio
from functools import partial
import logfire
import sentry_sdk
# from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.responses import RedirectResponse
from fastapi.responses import JSONResponse
from fastapi_mcp import FastApiMCP
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.database.database import async_session, get_db
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.routes import (
    auth_routes,
    user_routes,
    payment_routes,
    item_routes,
    order_routes,
    product_routes,
    marketplace_routes,
    review_routes,
    report_routes,
    settings_routes,
    stats_routes,
    ws_routes,
    audit_log_routes,
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


sentry_sdk.init(
    dsn="https://29793bfca41a89530399ca6c906484a8@o4505603287023616.ingest.us.sentry.io/4509700294311936",
    send_default_pii=True,
    traces_sample_rate=1.0,
    profile_session_sample_rate=1.0,
    profile_lifecycle="trace",
)


app = FastAPI(
    title="ServiPal",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    debug=True,
    summary="Item delivery, food ordering, P2P and laundry services application.",
    contact={
        "name": "ServiPal",
        "url": "https://servi-pal.com",
        "email": "servipal@servi-pal.com",
    },
)

app.state.limiter = limiter.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


FAVICON_URL = "https://mohdelivery.s3.us-east-1.amazonaws.com/favion/favicon.ico"


# Override default Swagger UI with custom favicon
@app.get("/docs", include_in_schema=False)
def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title="ServiPal API",
        # docs_url='/',
        swagger_favicon_url=FAVICON_URL,
    )


# Override default ReDoc with custom favicon (optional)
@app.get("/redoc", include_in_schema=False)
def custom_redoc_html():
    return get_redoc_html(
        openapi_url=app.openapi_url, title="ServiPal API", redoc_favicon_url=FAVICON_URL
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse(url=FAVICON_URL)


# Option 2: If you have a local favicon file, use this instead:
# @app.get("/favicon.ico", include_in_schema=False)
# def favicon():
#     return FileResponse(path="path/to/your/favicon.ico", media_type="image/x-icon")


# Your API routes go here
@app.get("/")
def read_root():
    return {"message": "Welcome to ServiPal API"}


logfire.configure(service_name="ServiPal")
logfire.debug("App Debug mode on")
logfire.instrument_fastapi(app=app)
logfire.instrument_sqlalchemy(engine=engine)

origins = ["http://localhost:3000", "https://servi-pal.com"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    # allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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



@app.get("/.well-known/assetlinks.json")
async def get_assetlinks():
    """
    Dynamic asset links configuration from environment variables
    """
    if not settings.FIN:
        # Return empty array if not configured (development mode)
        return JSONResponse(content=[])
    
    asset_links = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": settings.PACKAGE_NAME,
                "sha256_cert_fingerprints": [
                    settings.FINGER_PRINT
                ]
            }
        }
    ]
    
    return JSONResponse(
        content=asset_links,
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=86400"
        }
    )



app.include_router(auth_routes.router)
app.include_router(user_routes.router)
app.include_router(review_routes.router)
app.include_router(report_routes.router)
app.include_router(item_routes.router)
app.include_router(order_routes.router)
app.include_router(payment_routes.router)
app.include_router(product_routes.router)
app.include_router(marketplace_routes.router)
app.include_router(settings_routes.router)
app.include_router(stats_routes.router)
app.include_router(ws_routes.router)
app.include_router(audit_log_routes.router)


# mcp = FastApiMCP(app, include_tags=["Notifications", "Reports"])

# mcp.mount()
