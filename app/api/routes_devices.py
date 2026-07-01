from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import json

from app.db import get_db
from app.models import Device, Event, AuditLog
from app.schemas import RenameRequest, NotesRequest

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

@router.post("/{device_id}/trust")
def trust_device(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    device.status = "trusted"
    device.updated_at = datetime.utcnow()
    log_action(db, device, "trust", {"previous_status": device.status})
    db.commit()
    return {"status": "success", "device_id": device.id}

@router.post("/{device_id}/ignore")
def ignore_device(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    device.status = "ignored"
    device.updated_at = datetime.utcnow()
    log_action(db, device, "ignore", {"previous_status": device.status})
    db.commit()
    return {"status": "success", "device_id": device.id}

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
