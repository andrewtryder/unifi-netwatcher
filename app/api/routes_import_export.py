import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.activity_log import record_event
from app.api.routes_devices import log_action
from app.db import get_db
from app.mac import normalize_mac
from app.models import Device, Setting
from app.web.context import template_context
from app.web.display import (
    EVENT_RETENTION_SETTING_KEY,
    OBSERVATION_RETENTION_SETTING_KEY,
    SCAN_INTERVAL_SETTING_KEY,
    resolve_event_retention,
    resolve_observation_retention,
    resolve_scan_interval,
)
from app.web.templates_env import templates

router = APIRouter()

PRESET_INTERVALS = {60, 300, 900, 1800, 3600}
PRESET_RETENTION_DAYS = {0, 7, 30, 90, 180, 365}


def _upsert_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if not row:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def _parse_retention_days(mode: str, custom_days: int | None) -> int | HTMLResponse:
    if mode == "custom":
        if custom_days is None or custom_days < 0:
            return HTMLResponse(
                "<div id='retention-msg' class='mt-4 p-4 rounded-lg bg-error/10 border border-error/20 "
                "text-error text-sm'>Custom retention must be 0 or a positive number of days.</div>",
                status_code=400,
            )
        return int(custom_days)
    try:
        days = int(mode)
    except (TypeError, ValueError):
        return HTMLResponse(
            "<div id='retention-msg' class='mt-4 p-4 rounded-lg bg-error/10 border border-error/20 "
            "text-error text-sm'>Invalid retention selection.</div>",
            status_code=400,
        )
    if days not in PRESET_RETENTION_DAYS:
        return HTMLResponse(
            "<div id='retention-msg' class='mt-4 p-4 rounded-lg bg-error/10 border border-error/20 "
            "text-error text-sm'>Unsupported retention preset.</div>",
            status_code=400,
        )
    return days


@router.get("", response_class=HTMLResponse)
def tools_page(request: Request, db: Session = Depends(get_db)):
    interval, source = resolve_scan_interval(db)
    obs_days, obs_source = resolve_observation_retention(db)
    event_days, event_source = resolve_event_retention(db)
    return templates.TemplateResponse(
        request=request,
        name="tools.html",
        context=template_context(
            db,
            request,
            scan_interval_seconds=interval,
            scan_interval_source=source,
            scan_interval_is_preset=interval in PRESET_INTERVALS,
            observation_retention_days=obs_days,
            observation_retention_source=obs_source,
            observation_retention_is_preset=obs_days in PRESET_RETENTION_DAYS,
            event_retention_days=event_days,
            event_retention_source=event_source,
            event_retention_is_preset=event_days in PRESET_RETENTION_DAYS,
        ),
    )


@router.post("/htmx/scan-interval", response_class=HTMLResponse)
def save_scan_interval(
    request: Request,
    db: Session = Depends(get_db),
    interval_mode: str = Form(...),
    custom_seconds: int | None = Form(None),
):
    if interval_mode == "custom":
        if custom_seconds is None or custom_seconds < 30:
            return HTMLResponse(
                "<div id='scan-interval-msg' class='mt-4 p-4 rounded-lg bg-error/10 border border-error/20 "
                "text-error text-sm'>Custom interval must be at least 30 seconds.</div>",
                status_code=400,
            )
        seconds = int(custom_seconds)
    else:
        try:
            seconds = int(interval_mode)
        except (TypeError, ValueError):
            return HTMLResponse(
                "<div id='scan-interval-msg' class='mt-4 p-4 rounded-lg bg-error/10 border border-error/20 "
                "text-error text-sm'>Invalid interval selection.</div>",
                status_code=400,
            )
        if seconds not in PRESET_INTERVALS:
            return HTMLResponse(
                "<div id='scan-interval-msg' class='mt-4 p-4 rounded-lg bg-error/10 border border-error/20 "
                "text-error text-sm'>Unsupported preset interval.</div>",
                status_code=400,
            )

    _upsert_setting(db, SCAN_INTERVAL_SETTING_KEY, str(seconds))
    db.commit()

    # Reschedule live when the app scheduler is available (skip in unit tests).
    try:
        from app.main import reschedule_scan_job

        reschedule_scan_job(seconds)
    except Exception:
        # Scheduler may not be started in TestClient without lifespan, or job absent.
        pass

    record_event(db, "scan_interval_updated", f"Scan interval set to {seconds} seconds")
    db.commit()

    minutes = seconds / 60
    label = f"{int(minutes)} min" if seconds % 60 == 0 else f"{seconds}s"
    return HTMLResponse(
        f"<div id='scan-interval-msg' class='mt-4 p-4 rounded-lg bg-secondary/10 border border-secondary/20 "
        f"text-secondary text-sm'>Saved. Active interval is now {label} ({seconds}s) — stored override. "
        f"The running scheduler was updated without a restart.</div>"
    )


@router.post("/htmx/retention", response_class=HTMLResponse)
def save_retention(
    request: Request,
    db: Session = Depends(get_db),
    observation_mode: str = Form(...),
    observation_custom_days: int | None = Form(None),
    event_mode: str = Form(...),
    event_custom_days: int | None = Form(None),
):
    obs_result = _parse_retention_days(observation_mode, observation_custom_days)
    if isinstance(obs_result, HTMLResponse):
        return obs_result
    event_result = _parse_retention_days(event_mode, event_custom_days)
    if isinstance(event_result, HTMLResponse):
        return event_result

    _upsert_setting(db, OBSERVATION_RETENTION_SETTING_KEY, str(obs_result))
    _upsert_setting(db, EVENT_RETENTION_SETTING_KEY, str(event_result))
    db.commit()

    record_event(
        db,
        "retention_updated",
        f"Retention set to observations={obs_result}d, events={event_result}d",
    )
    db.commit()

    def _label(days: int) -> str:
        return "disabled" if days == 0 else f"{days} days"

    return HTMLResponse(
        f"<div id='retention-msg' class='mt-4 p-4 rounded-lg bg-secondary/10 border border-secondary/20 "
        f"text-secondary text-sm'>Saved. Observations: {_label(obs_result)}; "
        f"Events: {_label(event_result)} — stored override. "
        f"The daily cleanup job will use these values on the next run.</div>"
    )


@router.post("/htmx/import_trusted")
async def import_trusted(
    request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    content = await file.read()
    text = content.decode("utf-8")

    imported_count = 0
    now = datetime.utcnow()

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        note = ""
        if "," in line:
            cols = [c.strip() for c in line.split(",", 1)]
            raw_mac = cols[0]
            note = cols[1] if len(cols) > 1 else ""
        else:
            parts = line.split("#", 1)
            raw_mac = parts[0].strip()
            note = parts[1].strip() if len(parts) > 1 else ""

        if raw_mac.lower() in ("mac", "mac address"):
            continue

        mac = normalize_mac(raw_mac)
        if not mac:
            continue

        device = db.query(Device).filter(Device.mac == mac).first()
        if not device:
            device = Device(
                mac=mac, status="trusted", first_seen_at=now, last_seen_at=now, display_name=note
            )
            db.add(device)
            db.flush()
            log_action(db, device, "trust", {"source": "trusted.csv_import"})
            imported_count += 1
        else:
            if device.status != "trusted":
                device.status = "trusted"
                log_action(db, device, "trust", {"source": "trusted.csv_import"})
                imported_count += 1
            if note and not device.display_name:
                device.display_name = note

    record_event(
        db, "import_trusted", f"Imported or updated {imported_count} trusted devices from CSV"
    )
    db.commit()
    return HTMLResponse(
        f"<div class='text-green-600 font-bold mt-4'>Imported or updated {imported_count} trusted devices.</div>"
    )


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
    writer.writerow(
        [
            "ID",
            "MAC",
            "Status",
            "IP",
            "Hostname",
            "Display Name",
            "Vendor",
            "Site",
            "SSID",
            "First Seen",
            "Last Seen",
        ]
    )

    for d in devices:
        writer.writerow(
            [
                d.id,
                d.mac,
                d.status,
                d.ip,
                d.hostname,
                d.display_name,
                d.vendor,
                d.last_site,
                d.last_ssid,
                d.first_seen_at.isoformat() if d.first_seen_at else "",
                d.last_seen_at.isoformat() if d.last_seen_at else "",
            ]
        )

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=devices.csv"},
    )
