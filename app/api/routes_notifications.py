import json
import logging
from html import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.activity_log import record_event
from app.db import get_db
from app.models import NotificationChannel
from app.notifications import PROVIDERS
from app.notifications.schemas import parse_notification_config
from app.security.crypto_migrate import load_channel_config, store_channel_config
from app.security.secrets import SecretKeyError
from app.web.context import template_context
from app.web.templates_env import templates

router = APIRouter()
logger = logging.getLogger(__name__)


def _error_html(message: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(
        f"<span class='text-red-500'>{escape(message)}</span>",
        status_code=status_code,
    )


@router.post("/htmx/create")
def create_channel(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    config_json: str = Form(...),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name or len(name) > 128:
        return _error_html("Channel name must be 1–128 characters")
    if type not in PROVIDERS:
        return _error_html("Invalid provider type")

    try:
        normalized = parse_notification_config(type, config_json)
    except (ValidationError, ValueError) as exc:
        logger.warning("Invalid notification channel configuration for %s: %s", type, exc)
        return _error_html("Invalid channel configuration")

    provider = PROVIDERS[type]
    if not provider.validate_config(normalized):
        return _error_html("Channel configuration failed provider validation")

    remove_empty = db.query(NotificationChannel).count() == 0
    channel = NotificationChannel(name=name, type=type, config_json={})
    store_channel_config(channel, normalized)
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
    if not channel:
        return HTMLResponse("Not found", 404)

    provider = PROVIDERS.get(channel.type)
    if not provider:
        return _error_html("Unknown provider", 400)

    try:
        config = load_channel_config(channel)
    except (SecretKeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load channel config for channel %d: %s", channel_id, exc)
        return _error_html(
            "Stored channel config is invalid or the encryption key has changed", 400
        )

    if not provider.validate_config(config):
        return _error_html("Stored channel config failed validation", 400)

    success, sc, resp, err = provider.send("NetWatcher Test Message!", config)

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
    return HTMLResponse(
        f"<span class='text-error text-xs font-bold'>Failed: {escape(str(err or sc))}</span>"
    )
