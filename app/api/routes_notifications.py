import html
import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.activity_log import record_event
from app.db import get_db
from app.models import NotificationChannel
from app.notifications import PROVIDERS
from app.notifications.secrets import encrypt_channel_config, redact_config_for_log
from app.web.context import template_context
from app.web.templates_env import templates

router = APIRouter()


@router.post("/htmx/create")
def create_channel(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    config_json: str = Form(...),
    db: Session = Depends(get_db),
):
    if type not in PROVIDERS:
        return HTMLResponse(
            "<span class='text-red-500'>Invalid provider type</span>", status_code=400
        )
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError:
        return HTMLResponse(
            "<span class='text-red-500'>Invalid JSON config</span>", status_code=400
        )

    provider = PROVIDERS[type]
    if not provider.validate_config(config):
        return HTMLResponse(
            "<span class='text-red-500'>Invalid provider configuration</span>",
            status_code=400,
        )

    remove_empty = db.query(NotificationChannel).count() == 0
    channel = NotificationChannel(
        name=name,
        type=type,
        config_json=encrypt_channel_config(config),
    )
    db.add(channel)
    db.flush()
    record_event(
        db,
        "notification_created",
        f"Created alert channel: {name} ({type})",
        metadata={"config": redact_config_for_log(config)},
    )
    db.commit()
    return templates.TemplateResponse(
        request=request,
        name="partials/notification_create_response.html",
        context={**template_context(db, request), "channel": channel, "remove_empty": remove_empty},
    )


@router.post("/htmx/{channel_id}/delete")
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = db.query(NotificationChannel).filter(NotificationChannel.id == channel_id).first()
    if channel:
        record_event(db, "notification_deleted", f"Deleted alert channel: {channel.name}")
        db.delete(channel)
        db.commit()
    return HTMLResponse("")


@router.post("/htmx/{channel_id}/test")
def test_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = db.query(NotificationChannel).filter(NotificationChannel.id == channel_id).first()
    if not channel:
        return HTMLResponse("Not found", 404)

    provider = PROVIDERS.get(channel.type)
    if not provider:
        return HTMLResponse("Provider err", 400)

    from app.notifications.secrets import decrypt_channel_config

    try:
        config = decrypt_channel_config(channel.config_json)
    except Exception:
        return HTMLResponse(
            "<span class='text-error text-xs font-bold'>Failed: invalid stored config</span>",
            status_code=400,
        )

    if not provider.validate_config(config):
        return HTMLResponse(
            "<span class='text-error text-xs font-bold'>Failed: invalid config</span>",
            status_code=400,
        )

    success, sc, resp, err = provider.send("NetWatcher Test Message!", config)

    safe_detail = html.escape(str(err or sc))
    record_event(
        db,
        "notification_test",
        f"Test alert for channel {channel.name}: {'OK' if success else 'failed'}",
        severity="info" if success else "warning",
        metadata={"channel_id": channel.id, "status_code": sc},
    )
    db.commit()

    if success:
        return HTMLResponse("<span class='text-secondary text-xs font-bold'>Test OK!</span>")
    return HTMLResponse(f"<span class='text-error text-xs font-bold'>Failed: {safe_detail}</span>")
