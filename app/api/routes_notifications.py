import json
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse

from app.db import get_db
from app.models import NotificationChannel
from app.notifications import PROVIDERS
from app.activity_log import record_event
from app.web.context import template_context
from app.web.templates_env import templates

router = APIRouter()

@router.post("/htmx/create")
def create_channel(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    config_json: str = Form(...),
    db: Session = Depends(get_db)
):
    if type not in PROVIDERS:
        return HTMLResponse("<span class='text-red-500'>Invalid provider type</span>", status_code=400)
    try:
        conf = json.loads(config_json)
    except json.JSONDecodeError:
        return HTMLResponse("<span class='text-red-500'>Invalid JSON config</span>", status_code=400)

    remove_empty = db.query(NotificationChannel).count() == 0
    channel = NotificationChannel(name=name, type=type, config_json=config_json)
    db.add(channel)
    db.flush()
    record_event(db, "notification_created", f"Created alert channel: {name} ({type})")
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
    if not channel: return HTMLResponse("Not found", 404)
    
    provider = PROVIDERS.get(channel.type)
    if not provider: return HTMLResponse("Provider err", 400)
    
    success, sc, resp, err = provider.send("NetWatcher Test Message!", json.loads(channel.config_json))

    record_event(
        db,
        "notification_test",
        f"Test alert for channel {channel.name}: {'OK' if success else err or sc}",
        severity="info" if success else "warning",
        metadata={"channel_id": channel.id, "status_code": sc},
    )
    db.commit()
    
    if success:
        return HTMLResponse("<span class='text-secondary text-xs font-bold'>Test OK!</span>")
    else:
        return HTMLResponse(f"<span class='text-error text-xs font-bold'>Failed: {err or sc}</span>")
