from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime
import json

from app.db import get_db
from app.models import Device, Event, AuditLog, Observation, NotificationDelivery
from app.schemas import RenameRequest, NotesRequest, BulkDeviceIdsRequest
from app.activity_log import record_event
from app.web.context import template_context
from app.web.templates_env import templates

router = APIRouter()

def get_device_or_404(db: Session, device_id: int):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device

def log_action(db: Session, device: Device, action: str, details: dict):
    # Log to AuditLog
    audit = AuditLog(
        actor="system",  # placeholder until auth is implemented
        action=action,
        target_type="device",
        target_id=str(device.id),
        details_json=json.dumps(details)
    )
    db.add(audit)
    
    # Log to Event
    event_type = action
    if action == "trust": event_type = "trusted"
    elif action == "ignore": event_type = "ignored"
    elif action == "rename": event_type = "renamed"
    elif action == "notes": event_type = "note_added"
    
    event = Event(
        device_id=device.id,
        event_type=event_type,
        severity="info",
        message=f"Device {device.mac} {action}: {details}"
    )
    db.add(event)

@router.post("/bulk/trust")
def bulk_trust(req: BulkDeviceIdsRequest, db: Session = Depends(get_db)):
    updated = 0
    for device_id in req.device_ids:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            continue
        previous_status = device.status
        device.status = "trusted"
        device.updated_at = datetime.utcnow()
        log_action(db, device, "trust", {"previous_status": previous_status, "bulk": True})
        updated += 1
    db.commit()
    return {"status": "success", "updated": updated}

@router.post("/bulk/ignore")
def bulk_ignore(req: BulkDeviceIdsRequest, db: Session = Depends(get_db)):
    updated = 0
    for device_id in req.device_ids:
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            continue
        previous_status = device.status
        device.status = "ignored"
        device.updated_at = datetime.utcnow()
        log_action(db, device, "ignore", {"previous_status": previous_status, "bulk": True})
        updated += 1
    db.commit()
    return {"status": "success", "updated": updated}

@router.post("/{device_id}/trust")
def trust_device(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    previous_status = device.status
    device.status = "trusted"
    device.updated_at = datetime.utcnow()
    log_action(db, device, "trust", {"previous_status": previous_status})
    db.commit()
    return {"status": "success", "device_id": device.id}

@router.post("/{device_id}/ignore")
def ignore_device(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    previous_status = device.status
    device.status = "ignored"
    device.updated_at = datetime.utcnow()
    log_action(db, device, "ignore", {"previous_status": previous_status})
    db.commit()
    return {"status": "success", "device_id": device.id}

@router.post("/{device_id}/reset")
def reset_device(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    previous_status = device.status
    device.status = "unknown"
    device.updated_at = datetime.utcnow()
    log_action(db, device, "reset", {"previous_status": previous_status})
    db.commit()
    return {"status": "success", "device_id": device.id}

@router.post("/{device_id}/delete")
def delete_device(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    mac = device.mac

    record_event(
        db,
        "deleted",
        f"Device deleted: {mac}",
        metadata={"mac": mac, "status": device.status, "device_id": device.id},
    )

    event_ids = [
        e.id for e in db.query(Event).filter(Event.device_id == device.id).all()
    ]
    if event_ids:
        db.query(NotificationDelivery).filter(
            NotificationDelivery.event_id.in_(event_ids)
        ).delete(synchronize_session=False)
    db.query(Observation).filter(Observation.device_id == device.id).delete(synchronize_session=False)
    db.query(Event).filter(Event.device_id == device.id).delete(synchronize_session=False)

    db.add(AuditLog(
        actor="system",
        action="delete",
        target_type="device",
        target_id=str(device.id),
        details_json=json.dumps({"mac": mac, "status": device.status}),
    ))
    db.delete(device)
    db.commit()
    return {"status": "success", "deleted_device_id": device_id, "mac": mac}

@router.post("/{device_id}/rename")
def rename_device(device_id: int, req: RenameRequest, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    old_name = device.display_name
    device.display_name = req.display_name
    device.updated_at = datetime.utcnow()
    log_action(db, device, "rename", {"old_name": old_name, "new_name": req.display_name})
    db.commit()
    return {"status": "success", "device_id": device.id}

@router.post("/{device_id}/notes")
def update_device_notes(device_id: int, req: NotesRequest, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    device.notes = req.notes
    device.updated_at = datetime.utcnow()
    log_action(db, device, "notes", {"notes_length": len(req.notes)})
    db.commit()
    return {"status": "success", "device_id": device.id}

from app.unifi.client import UnifiClient
from app.config import settings
from fastapi.responses import HTMLResponse

@router.get("/htmx/{device_id}/block_modal")
def block_modal(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/block_modal.html",
        context={
            "request": request,
            "device": device,
            "dry_run_enabled": settings.UNIFI_DRY_RUN_BLOCKS,
        },
    )

@router.post("/htmx/{device_id}/block")
def block_device_action(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    
    client = UnifiClient()
    success = client.block_client(device.mac)
    
    if success:
        device.status = "blocked"
        device.updated_at = datetime.utcnow()
        log_action(db, device, "blocked", {"dry_run": settings.UNIFI_DRY_RUN_BLOCKS})
        db.commit()
        return templates.TemplateResponse(
            request=request,
            name="partials/block_success.html",
            context=template_context(db, request, device=device),
        )
    else:
        return HTMLResponse("<script>alert('Failed to execute block on controller.'); document.getElementById('modal-container').remove();</script>")

@router.post("/htmx/{device_id}/unblock")
def unblock_device_action(request: Request, device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    
    client = UnifiClient()
    success = client.unblock_client(device.mac)
    
    if success:
        device.status = "unknown"
        device.updated_at = datetime.utcnow()
        log_action(db, device, "unblocked", {"dry_run": settings.UNIFI_DRY_RUN_BLOCKS})
        db.commit()
        return templates.TemplateResponse(
            request=request,
            name="partials/block_success.html",
            context=template_context(db, request, device=device),
        )
    else:
        return HTMLResponse("<script>alert('Failed to execute unblock on controller.');</script>")
