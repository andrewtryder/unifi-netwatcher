from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import alembic.command
import alembic.config

from app.api.routes_scans import router as scans_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run alembic migrations on startup
    alembic_cfg = alembic.config.Config("alembic.ini")
    alembic.command.upgrade(alembic_cfg, "head")
    yield
    # Shutdown actions here if needed

app = FastAPI(title="NetWatcher for UniFi", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Include routers
app.include_router(scans_router, prefix="/api/scan", tags=["scans"])

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz():
    return {"status": "ready"}

# Basic index route for now
@app.get("/")
def index():
    return {"message": "NetWatcher is running. API is ready."}
