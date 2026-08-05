import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.routes_devices import router as devices_api_router
from app.api.routes_import_export import router as tools_router
from app.api.routes_notifications import router as notifications_router
from app.api.routes_scans import router as scans_router
from app.config import settings
from app.db import SessionLocal
from app.models import OuiEntry
from app.notifications.http import close_notification_http_client
from app.oui import update_oui_data
from app.retention import run_retention
from app.scanner import run_scan
from app.security.crypto_migrate import migrate_notification_secrets
from app.security.csp import CSPMiddleware
from app.security.middleware import AccessControlMiddleware
from app.security.models import SecuritySettings
from app.security.routes import router as security_router
from app.security.secrets import SecretKeyError, init_app_secrets
from app.security.service import ensure_security_settings
from app.unifi.client import close_unifi_client, get_unifi_client
from app.web.display import (
    resolve_event_retention,
    resolve_observation_retention,
    resolve_scan_interval,
)
from app.web.routes import router as web_router

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
SCAN_JOB_ID = "scheduled_scan"
RETENTION_JOB_ID = "retention_cleanup"


async def scheduled_oui_update():
    logger.info("Running scheduled OUI data update...")
    db = SessionLocal()
    try:
        await update_oui_data(db)
    finally:
        db.close()


def scheduled_scan():
    logger.info("Running scheduled scan...")
    db = SessionLocal()
    try:
        run_scan(db)
    except Exception:
        logger.exception("Scheduled scan failed")
    finally:
        db.close()


def scheduled_retention():
    logger.info("Running scheduled retention cleanup...")
    db = SessionLocal()
    try:
        run_retention(db)
    except Exception:
        logger.exception("Scheduled retention failed")
    finally:
        db.close()


def reschedule_scan_job(seconds: int) -> None:
    """Apply a new scan interval to the running APScheduler job (no restart)."""
    if seconds <= 0:
        raise ValueError("Scan interval must be positive")
    if scheduler.get_job(SCAN_JOB_ID):
        scheduler.reschedule_job(SCAN_JOB_ID, trigger="interval", seconds=seconds)
        logger.info("Rescheduled scan job to every %s seconds", seconds)
    else:
        scheduler.add_job(
            scheduled_scan,
            "interval",
            seconds=seconds,
            id=SCAN_JOB_ID,
            replace_existing=True,
        )
        logger.info("Added scan job every %s seconds", seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from pathlib import Path

    init_app_secrets(
        env_secret=settings.app_secret_key(),
        key_path=Path(settings.APP_SECRET_KEY_PATH),
        allow_generate=True,
    )

    db = SessionLocal()
    interval = settings.SCAN_INTERVAL_SECONDS
    obs_days = settings.OBSERVATION_RETENTION_DAYS
    event_days = settings.EVENT_RETENTION_DAYS
    try:
        ensure_security_settings(db)
        try:
            migrate_notification_secrets(db)
            from app.security.secrets import cleanup_key_rotation_backup

            cleanup_key_rotation_backup(Path(settings.APP_SECRET_KEY_PATH))
        except SecretKeyError:
            logger.exception("Notification secret migration failed")
            raise
        interval, source = resolve_scan_interval(db)
        obs_days, obs_source = resolve_observation_retention(db)
        event_days, event_source = resolve_event_retention(db)
        logger.info("Scan interval: %ss (%s)", interval, source)
        logger.info(
            "Retention: observations %sd (%s), events %sd (%s)",
            obs_days,
            obs_source,
            event_days,
            event_source,
        )
        if settings.SECURITY_RECOVERY_BYPASS:
            logger.warning(
                "SECURITY_RECOVERY_BYPASS is enabled — CIDR and trusted-host "
                "filtering are bypassed. Disable this flag after recovering access."
            )
    finally:
        db.close()

    scheduler.add_job(scheduled_oui_update, "interval", days=7)
    scheduler.add_job(
        scheduled_scan,
        "interval",
        seconds=interval,
        id=SCAN_JOB_ID,
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_retention,
        "interval",
        days=1,
        id=RETENTION_JOB_ID,
        replace_existing=True,
    )
    scheduler.start()

    # Warm shared HTTP clients (closed on shutdown)
    get_unifi_client()

    # Check if OUI DB is empty and trigger immediate download if so
    db = SessionLocal()
    try:
        if db.query(OuiEntry).count() == 0:
            logger.info("OUI database is empty. Triggering immediate download.")
            asyncio.create_task(scheduled_oui_update())
        if obs_days > 0 or event_days > 0:
            logger.info("Triggering startup retention cleanup.")
            scheduled_retention()
    finally:
        db.close()

    yield

    scheduler.shutdown()
    close_unifi_client()
    close_notification_http_client()


app = FastAPI(title="NetWatcher for UniFi", lifespan=lifespan)
app.add_middleware(AccessControlMiddleware)
app.add_middleware(CSPMiddleware)

# Mount static files
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Include routers
app.include_router(web_router, tags=["web"])
app.include_router(scans_router, prefix="/api/scan", tags=["scans"])
app.include_router(devices_api_router, prefix="/api/devices", tags=["devices"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["notifications"])
app.include_router(tools_router, prefix="/tools", tags=["tools"])
app.include_router(security_router, prefix="/security", tags=["security"])


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            if db.query(SecuritySettings).filter(SecuritySettings.id == 1).first() is None:
                return JSONResponse({"status": "not_ready"}, status_code=503)
        finally:
            db.close()
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return {"status": "ready"}
