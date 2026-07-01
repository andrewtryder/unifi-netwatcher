from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm import joinedload

from app.db import get_db
from app.models import Device, Event, Observation, NotificationChannel
from app.api.routes_devices import log_action
from app.web.context import template_context
from app.web.templates_env import templates

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    total = db.query(Device).count()
    unknown = db.query(Device).filter(Device.status == "unknown").count()
    trusted = db.query(Device).filter(Device.status == "trusted").count()
    ignored = db.query(Device).filter(Device.status == "ignored").count()
    
    last_scan = (
        db.query(Event)
        .filter(Event.event_type.in_(["scan_finished", "scan_failed"]))
        .order_by(Event.created_at.desc())
        .first()
    )
    
    return templates.TemplateResponse(request=request, name="dashboard.html", context=template_context(db, request, stats={
        "total": total,
        "unknown": unknown,
        "trusted": trusted,
        "ignored": ignored
    }, last_scan=last_scan))

@router.get("/unknown", response_class=HTMLResponse)
def unknown_devices(request: Request, db: Session = Depends(get_db)):
    devices = db.query(Device).filter(Device.status == "unknown").order_by(Device.last_seen_at.desc()).all()
    return templates.TemplateResponse(request=request, name="unknown.html", context=template_context(db, request, devices=devices))

@router.get("/devices", response_class=HTMLResponse)
def device_inventory(request: Request, db: Session = Depends(get_db)):
    devices = db.query(Device).order_by(Device.last_seen_at.desc()).all()
    return templates.TemplateResponse(request=request, name="devices.html", context=template_context(db, request, devices=devices))

@router.get("/devices/{device_id}", response_class=HTMLResponse)
def device_detail(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        return HTMLResponse("Not Found", status_code=404)
    observations = (
        db.query(Observation)
        .filter(Observation.device_id == device.id)
        .order_by(Observation.seen_at.desc())
        .limit(50)
        .all()
    )
    return templates.TemplateResponse(request=request, name="device_detail.html", context=template_context(db, request, device=device, observations=observations))

@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    events = (
        db.query(Event)
        .options(joinedload(Event.device))
        .order_by(Event.created_at.desc())
        .limit(250)
        .all()
    )
    return templates.TemplateResponse(request=request, name="logs.html", context=template_context(db, request, events=events))

@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, db: Session = Depends(get_db)):
    channels = db.query(NotificationChannel).all()
    return templates.TemplateResponse(request=request, name="notifications.html", context=template_context(db, request, channels=channels))

@router.get("/htmx/nav", response_class=HTMLResponse)
def htmx_nav(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="partials/nav_refresh.html",
        context=template_context(db, request),
    )

@router.get("/htmx/scan-status", response_class=HTMLResponse)
def htmx_scan_status(request: Request, db: Session = Depends(get_db)):
    last_scan = (
        db.query(Event)
        .filter(Event.event_type.in_(["scan_finished", "scan_failed"]))
        .order_by(Event.created_at.desc())
        .first()
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/scan_status.html",
        context=template_context(db, request, last_scan=last_scan),
    )

def _get_device_or_404(db: Session, device_id: int) -> Device:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Device not found")
    return device

@router.get("/htmx/devices/{device_id}/status-badge", response_class=HTMLResponse)
def htmx_device_status_badge(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = _get_device_or_404(db, device_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/device_status_badge.html",
        context=template_context(db, request, device=device),
    )

@router.get("/htmx/devices/{device_id}/actions", response_class=HTMLResponse)
def htmx_device_actions(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = _get_device_or_404(db, device_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/device_actions.html",
        context=template_context(db, request, device=device),
    )

# HTMX actions for simple server-rendered flows
@router.post("/htmx/devices/{device_id}/trust")
def htmx_trust(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if device:
        device.status = "trusted"
        log_action(db, device, "trust", {})
        db.commit()
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
    return HTMLResponse(f"<span id='name-{device_id}'>{display_name}</span>")
