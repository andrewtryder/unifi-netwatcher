from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.unifi.factory import get_adapter_info, get_unifi_adapter
from app.unifi.models import UniFiError
from app.unifi.official import OfficialUniFiAdapter
from app.unifi.status import get_unifi_connection_status, sanitize_unifi_error

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status")
def unifi_status(db: Session = Depends(get_db)):
    """Return structured connection and diagnostic status for UniFi integration."""
    status = get_unifi_connection_status(db)
    return status.to_dict()


@router.post("/test-connection")
def test_connection():
    """Trigger an isolated, synchronous connection and capability test against the UniFi controller.

    Does not trigger background scanning, database changes, or notifications.
    """
    try:
        try:
            adapter = get_unifi_adapter()
            success, raw_message = adapter.test_connection()
            clean_message = sanitize_unifi_error(raw_message)
            adapter_name = "official" if isinstance(adapter, OfficialUniFiAdapter) else "legacy"
            capabilities = {
                "can_block": adapter.capabilities.can_block,
                "can_unblock": adapter.capabilities.can_unblock,
                "has_wifi_telemetry": adapter.capabilities.has_wifi_telemetry,
                "has_switch_port": adapter.capabilities.has_switch_port,
                "supports_pagination": adapter.capabilities.supports_pagination,
            }
            mock_mode = bool(adapter.mock_mode)
        except UniFiError as exc:
            # Typed UniFi errors during adapter creation/probing (e.g. 401/403 in auto mode)
            success = False
            clean_message = sanitize_unifi_error(str(exc))
            _, adapter_name, caps = get_adapter_info()
            capabilities = {
                "can_block": caps.can_block,
                "can_unblock": caps.can_unblock,
                "has_wifi_telemetry": caps.has_wifi_telemetry,
                "has_switch_port": caps.has_switch_port,
                "supports_pagination": caps.supports_pagination,
            }
            mock_mode = bool(settings.UNIFI_MOCK_MODE)

        return {
            "success": bool(success),
            "message": clean_message,
            "adapter": adapter_name,
            "configured_mode": settings.UNIFI_API_MODE,
            "configured_site": settings.UNIFI_SITE or "default",
            "mock_mode": mock_mode,
            "capabilities": capabilities,
        }
    except Exception as exc:
        logger.exception("Unexpected error executing UniFi connection test: %s", exc)
        _, adapter_name, _ = get_adapter_info()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Unexpected error executing connection test. Check application logs for details.",
                "adapter": adapter_name,
                "configured_mode": settings.UNIFI_API_MODE,
                "configured_site": settings.UNIFI_SITE or "default",
                "mock_mode": bool(settings.UNIFI_MOCK_MODE),
            },
        )
