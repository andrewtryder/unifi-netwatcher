from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_db
from app.models import Device, Event
from app.api.routes_devices import log_action

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")
templates.env.cache = None  # Disable Jinja caching to avoid unhashable dict errors in tests

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    total = db.query(Device).count()
    unknown = db.query(Device).filter(Device.status == "unknown").count()
    trusted = db.query(Device).filter(Device.status == "trusted").count()
    ignored = db.query(Device).filter(Device.status == "ignored").count()
    
    last_scan = db.query(Event).filter(Event.event_type == "scan_finished").order_by(Event.created_at.desc()).first()
    
    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "request": request,
        "stats": {
            "total": total,
            "unknown": unknown,
            "trusted": trusted,
            "ignored": ignored
        },
        "last_scan": last_scan
    })

@router.get("/unknown", response_class=HTMLResponse)
def unknown_devices(request: Request, db: Session = Depends(get_db)):
    devices = db.query(Device).filter(Device.status == "unknown").order_by(Device.last_seen_at.desc()).all()
    return templates.TemplateResponse(request=request, name="unknown.html", context={"request": request, "devices": devices})

@router.get("/devices", response_class=HTMLResponse)
def device_inventory(request: Request, db: Session = Depends(get_db)):
    devices = db.query(Device).order_by(Device.last_seen_at.desc()).all()
    return templates.TemplateResponse(request=request, name="devices.html", context={"request": request, "devices": devices})

@router.get("/devices/{device_id}", response_class=HTMLResponse)
def device_detail(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        return HTMLResponse("Not Found", status_code=404)
    return templates.TemplateResponse(request=request, name="device_detail.html", context={"request": request, "device": device})

# HTMX actions for simple server-rendered flows
@router.post("/htmx/devices/{device_id}/trust")
def htmx_trust(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if device:
        device.status = "trusted"
        log_action(db, device, "trust", {})
        db.commit()
    # Return nothing, HTMX will remove the row or update UI
    return HTMLResponse("")

@router.post("/htmx/devices/{device_id}/ignore")
def htmx_ignore(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if device:
        device.status = "ignored"
        log_action(db, device, "ignore", {})
        db.commit()
    return HTMLResponse("")

@router.post("/htmx/devices/{device_id}/rename")
def htmx_rename(request: Request, device_id: int, display_name: str = Form(...), db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if device:
        old_name = device.display_name
        device.display_name = display_name
        log_action(db, device, "rename", {"old_name": old_name, "new_name": display_name})
        db.commit()
    # return the new name fragment
    return HTMLResponse(f"<span id='name-{device_id}'>{display_name}</span>")

from app.models import NotificationChannel

@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, db: Session = Depends(get_db)):
    channels = db.query(NotificationChannel).all()
    return templates.TemplateResponse(request=request, name="notifications.html", context={"request": request, "channels": channels})
