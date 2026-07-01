from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import alembic.command
import alembic.config

from app.api.routes_scans import router as scans_router
from app.api.routes_devices import router as devices_api_router
from app.api.routes_notifications import router as notifications_router
from app.web.routes import router as web_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run alembic migrations on startup
    alembic_cfg = alembic.config.Config("alembic.ini")
    alembic.command.upgrade(alembic_cfg, "head")
    yield

app = FastAPI(title="NetWatcher for UniFi", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Include routers
app.include_router(web_router, tags=["web"])
app.include_router(scans_router, prefix="/api/scan", tags=["scans"])
app.include_router(devices_api_router, prefix="/api/devices", tags=["devices"])
app.include_router(notifications_router, prefix="/api/notifications", tags=["notifications"])

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz():
    return {"status": "ready"}
