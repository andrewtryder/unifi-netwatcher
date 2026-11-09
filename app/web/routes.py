from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import Device, Event, NotificationChannel, Observation
from app.schemas import RenameRequest
from app.services.context import request_context_from_request
from app.services.devices import DeviceNotFoundError, DeviceService
from app.services.notifications import get_behavioral_notification_rules
from app.web.context import template_context
from app.web.display import build_dashboard_metrics
from app.web.pagination import clamp_pagination, device_status_counts, paginate
from app.web.templates_env import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    counts = device_status_counts(db)

    last_scan = (
        db.query(Event)
        .filter(Event.event_type.in_(["scan_finished", "scan_failed"]))
        .order_by(Event.created_at.desc())
        .first()
    )

    recent_events = (
        db.query(Event)
        .options(joinedload(Event.device))
        .order_by(Event.created_at.desc())
        .limit(5)
        .all()
    )

    metrics = build_dashboard_metrics(db)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=template_context(
            db,
            request,
            device_counts=counts,
            stats=counts,
            last_scan=last_scan,
            recent_events=recent_events,
            **metrics,
        ),
    )


@router.get("/unknown", response_class=HTMLResponse)
def unknown_devices(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    page, page_size = clamp_pagination(page, page_size)
    query = db.query(Device).filter(Device.status == "unknown").order_by(Device.last_seen_at.desc())
    result = paginate(query, page, page_size)
    return templates.TemplateResponse(
        request=request,
        name="unknown.html",
        context=template_context(
            db,
            request,
            devices=result.items,
            pagination=result,
            list_path="/unknown",
        ),
    )


@router.get("/devices", response_class=HTMLResponse)
def device_inventory(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    page, page_size = clamp_pagination(page, page_size)
    query = db.query(Device).order_by(Device.last_seen_at.desc())
    result = paginate(query, page, page_size)
    return templates.TemplateResponse(
        request=request,
        name="devices.html",
        context=template_context(
            db,
            request,
            devices=result.items,
            pagination=result,
            list_path="/devices",
        ),
    )


TIMELINE_EVENT_TYPES = (
    "discovered",
    "device_connected",
    "device_disconnected",
    "device_reconnected",
    "device_ssid_changed",
    "device_ap_changed",
    "device_network_changed",
)


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
    timeline_events = (
        db.query(Event)
        .filter(
            Event.device_id == device.id,
            Event.event_type.in_(TIMELINE_EVENT_TYPES),
        )
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(30)
        .all()
    )
    has_more_timeline = (
        db.query(Event)
        .filter(
            Event.device_id == device.id,
            Event.event_type.in_(TIMELINE_EVENT_TYPES),
        )
        .offset(30)
        .limit(1)
        .count()
        > 0
    )
    next_timeline_offset = 30 if has_more_timeline else None

    return templates.TemplateResponse(
        request=request,
        name="device_detail.html",
        context=template_context(
            db,
            request,
            device=device,
            observations=observations,
            timeline_events=timeline_events,
            next_timeline_offset=next_timeline_offset,
        ),
    )


@router.get("/htmx/devices/{device_id}/timeline", response_class=HTMLResponse)
def htmx_device_timeline(
    request: Request,
    device_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        return HTMLResponse("Not Found", status_code=404)

    events = (
        db.query(Event)
        .filter(
            Event.device_id == device.id,
            Event.event_type.in_(TIMELINE_EVENT_TYPES),
        )
        .order_by(Event.created_at.desc(), Event.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    has_more = (
        db.query(Event)
        .filter(
            Event.device_id == device.id,
            Event.event_type.in_(TIMELINE_EVENT_TYPES),
        )
        .offset(offset + len(events))
        .limit(1)
        .count()
        > 0
    )
    next_offset = offset + len(events) if has_more else None

    return templates.TemplateResponse(
        request=request,
        name="partials/device_timeline_items.html",
        context=template_context(
            db,
            request,
            device=device,
            events=events,
            next_offset=next_offset,
        ),
    )


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, db: Session = Depends(get_db)):
    events = (
        db.query(Event)
        .options(joinedload(Event.device))
        .order_by(Event.created_at.desc())
        .limit(250)
        .all()
    )
    return templates.TemplateResponse(
        request=request, name="logs.html", context=template_context(db, request, events=events)
    )


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, db: Session = Depends(get_db)):
    channels = db.query(NotificationChannel).all()
    rules = get_behavioral_notification_rules(db)
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context=template_context(db, request, channels=channels, rules=rules),
    )


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
    try:
        DeviceService(db, request_context_from_request(request)).trust(device_id)
    except DeviceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    db.commit()
    return HTMLResponse("")


@router.post("/htmx/devices/{device_id}/ignore")
def htmx_ignore(request: Request, device_id: int, db: Session = Depends(get_db)):
    try:
        DeviceService(db, request_context_from_request(request)).ignore(device_id)
    except DeviceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    db.commit()
    return HTMLResponse("")


@router.post("/htmx/devices/{device_id}/rename")
def htmx_rename(
    request: Request, device_id: int, display_name: str = Form(...), db: Session = Depends(get_db)
):
    try:
        name = RenameRequest(display_name=display_name).display_name
        DeviceService(db, request_context_from_request(request)).rename(device_id, name)
    except DeviceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid display name") from exc
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="partials/device_name.html",
        context={"request": request, "device_id": device_id, "display_name": name},
    )
