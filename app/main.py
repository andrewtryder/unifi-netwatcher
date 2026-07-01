
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import alembic.command
import alembic.config
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging

from app.api.routes_scans import router as scans_router
from app.api.routes_devices import router as devices_api_router
from app.api.routes_notifications import router as notifications_router
from app.api.routes_import_export import router as tools_router
from app.web.routes import router as web_router
from app.oui import update_oui_data
from app.db import SessionLocal
from app.models import OuiEntry

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

async def scheduled_oui_update():
    logger.info("Running scheduled OUI data update...")
    db = SessionLocal()
    try:
        await update_oui_data(db)
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run alembic migrations on startup
    alembic_cfg = alembic.config.Config("alembic.ini")
    alembic.command.upgrade(alembic_cfg, "head")

    # Initialize scheduler
    scheduler.add_job(scheduled_oui_update, 'interval', days=7)
    scheduler.start()

    # Check if OUI DB is empty and trigger immediate download if so
    db = SessionLocal()
    try:
        if db.query(OuiEntry).count() == 0:
            logger.info("OUI database is empty. Triggering immediate download.")
            asyncio.create_task(scheduled_oui_update())
    finally:
        db.close()

    yield

    scheduler.shutdown()

app = FastAPI(title="NetWatcher for UniFi", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Include routers
app.include_router(web_router, tags=["web"])
app.include_router(scans_router, prefix="/api/scan", tags=["scans"])
app.include_router(devices_api_router, prefix="/api/devices", tags=["devices"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["notifications"])
app.include_router(tools_router, prefix="/tools", tags=["tools"])

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz():
    return {"status": "ready"}
