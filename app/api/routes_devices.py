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

from app.unifi.client import UnifiClient
from app.config import settings
from fastapi.responses import HTMLResponse

@router.get("/htmx/{device_id}/block_modal")
def block_modal(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    html = f"""
    <div id="modal-container" class="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full z-50 flex items-center justify-center">
        <div class="bg-white p-6 border rounded shadow-lg w-96 relative">
            <h3 class="text-xl font-bold text-red-600 mb-4">Confirm Block Device</h3>
            <p class="text-gray-700 text-sm mb-4">Are you sure you want to block this device at the UniFi Controller level?</p>
            <ul class="text-sm font-mono bg-gray-50 p-2 rounded mb-4">
                <li>MAC: {device.mac}</li>
                <li>IP: {device.ip or 'N/A'}</li>
                <li>Name: {device.display_name or device.hostname or 'N/A'}</li>
                <li>Last Seen: {device.last_seen_at}</li>
            </ul>
            <p class="text-xs text-red-500 font-bold mb-6">
                {'[DRY RUN ENABLED - No actual UniFi API call will be made]' if settings.UNIFI_DRY_RUN_BLOCKS else 'This action will disconnect the client from the network.'}
            </p>
            <div class="flex justify-end space-x-2">
                <button onclick="document.getElementById('modal-container').remove()" class="bg-gray-300 text-gray-800 px-4 py-2 rounded">Cancel</button>
                <button hx-post="/api/devices/htmx/{device.id}/block" hx-target="#modal-container" hx-swap="outerHTML" class="bg-red-600 text-white px-4 py-2 rounded font-bold">Block Device</button>
            </div>
        </div>
    </div>
    """
    return HTMLResponse(html)

@router.post("/htmx/{device_id}/block")
def block_device_action(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    
    client = UnifiClient()
    success = client.block_client(device.mac)
    
    if success:
        device.status = "blocked"
        device.updated_at = datetime.utcnow()
        log_action(db, device, "blocked", {"dry_run": settings.UNIFI_DRY_RUN_BLOCKS})
        db.commit()
        return HTMLResponse("<script>window.location.reload()</script>")
    else:
        return HTMLResponse("<script>alert('Failed to execute block on controller.'); document.getElementById('modal-container').remove();</script>")

@router.post("/htmx/{device_id}/unblock")
def unblock_device_action(device_id: int, db: Session = Depends(get_db)):
    device = get_device_or_404(db, device_id)
    
    client = UnifiClient()
    success = client.unblock_client(device.mac)
    
    if success:
        device.status = "unknown"
        device.updated_at = datetime.utcnow()
        log_action(db, device, "unblocked", {"dry_run": settings.UNIFI_DRY_RUN_BLOCKS})
        db.commit()
        return HTMLResponse("<script>window.location.reload()</script>")
    else:
        return HTMLResponse("<script>alert('Failed to execute unblock on controller.');</script>")
