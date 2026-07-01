import csv
import io
from datetime import datetime
from fastapi import APIRouter, Depends, UploadFile, File, Request
from fastapi.responses import PlainTextResponse, StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device
from app.mac import normalize_mac
from app.api.routes_devices import log_action

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

@router.get("/tools", response_class=HTMLResponse)
def tools_page(request: Request):
    return templates.TemplateResponse("tools.html", {"request": request})

@router.post("/htmx/import_trusted")
async def import_trusted(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    text = content.decode("utf-8")
    
    imported_count = 0
    now = datetime.utcnow()
    
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
            
        parts = line.split("#", 1)
        raw_mac = parts[0].strip()
        note = parts[1].strip() if len(parts) > 1 else ""
        
        mac = normalize_mac(raw_mac)
        if not mac:
            continue
            
        device = db.query(Device).filter(Device.mac == mac).first()
        if not device:
            device = Device(mac=mac, status="trusted", first_seen_at=now, last_seen_at=now, display_name=note)
            db.add(device)
            db.flush()
            log_action(db, device, "trust", {"source": "trusted.txt_import"})
            imported_count += 1
        else:
            if device.status != "trusted":
                device.status = "trusted"
                log_action(db, device, "trust", {"source": "trusted.txt_import"})
                imported_count += 1
            if note and not device.display_name:
                device.display_name = note
                
    db.commit()
    return HTMLResponse(f"<div class='text-green-600 font-bold mt-4'>Imported or updated {imported_count} trusted devices.</div>")

@router.get("/export/trusted", response_class=PlainTextResponse)
def export_trusted(db: Session = Depends(get_db)):
    devices = db.query(Device).filter(Device.status == "trusted").all()
    lines = ["# Trusted Devices Export"]
    for d in devices:
        name = d.display_name or d.hostname or d.notes or "Unknown"
        lines.append(f"{d.mac} # {name}")
    return "\n".join(lines) + "\n"

@router.get("/export/csv")
def export_csv(db: Session = Depends(get_db)):
    devices = db.query(Device).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "MAC", "Status", "IP", "Hostname", "Display Name", "Vendor", "Site", "SSID", "First Seen", "Last Seen"])
    
    for d in devices:
        writer.writerow([
            d.id, d.mac, d.status, d.ip, d.hostname, d.display_name, 
            d.vendor, d.last_site, d.last_ssid, 
            d.first_seen_at.isoformat() if d.first_seen_at else "", 
            d.last_seen_at.isoformat() if d.last_seen_at else ""
        ])
        
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=devices.csv"}
    )
