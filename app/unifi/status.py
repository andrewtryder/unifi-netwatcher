from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Event
from app.unifi.factory import get_adapter_info
from app.unifi.models import UniFiCapabilities


@dataclass(frozen=True)
class UniFiConnectionStatus:
    """Read-only summary of UniFi API integration state, adapter capabilities, and scan health."""

    configured_mode: str
    active_adapter: str
    configured_site: str
    connection_state: str  # "unknown" | "successful" | "degraded" | "failed"
    last_scan_at: datetime | None
    last_scan_result: bool | None
    last_successful_scan_at: datetime | None
    last_error: str | None
    capabilities: UniFiCapabilities
    mock_mode: bool
    dry_run_blocks: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize status model to dictionary suitable for JSON responses."""
        return {
            "configured_mode": self.configured_mode,
            "active_adapter": self.active_adapter,
            "configured_site": self.configured_site,
            "connection_state": self.connection_state,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "last_scan_result": self.last_scan_result,
            "last_successful_scan_at": (
                self.last_successful_scan_at.isoformat() if self.last_successful_scan_at else None
            ),
            "last_error": self.last_error,
            "capabilities": asdict(self.capabilities),
            "mock_mode": self.mock_mode,
            "dry_run_blocks": self.dry_run_blocks,
        }


def sanitize_unifi_error(message: str | None) -> str | None:
    """Sanitize error messages to prevent leaking credentials, API keys, or long stack traces."""
    if not message:
        return None

    cleaned = str(message)

    # Redact configured API key if present
    api_key = settings.unifi_api_key().strip()
    if api_key and api_key in cleaned:
        cleaned = cleaned.replace(api_key, "***")

    # Redact configured password if present
    password = settings.unifi_password().strip()
    if password and password in cleaned:
        cleaned = cleaned.replace(password, "***")

    # Redact app secret key if present
    app_secret = settings.app_secret_key().strip()
    if app_secret and app_secret in cleaned:
        cleaned = cleaned.replace(app_secret, "***")

    # Redact basic auth in URLs (e.g. https://user:pass@host)
    cleaned = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", cleaned)

    # Redact sensitive query parameters (token, key, password, secret, auth)
    cleaned = re.sub(
        r"(token|key|password|secret|auth)=([^&]+)", r"\1=***", cleaned, flags=re.IGNORECASE
    )

    # Redact sensitive headers (X-API-KEY, Authorization, Bearer, Cookie)
    cleaned = re.sub(
        r"(X-API-KEY|Authorization|Bearer|Cookie):\s*([^\s,]+)",
        r"\1: ***",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Shorten tracebacks if message contains multiline traceback
    if "Traceback (most recent call last):" in cleaned:
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        last_line = lines[-1] if lines else "Internal controller communication error"
        cleaned = f"Error: {last_line}"

    # Truncate overly long error messages
    if len(cleaned) > 250:
        cleaned = cleaned[:247] + "..."

    return cleaned


def get_unifi_connection_status(db: Session | None = None) -> UniFiConnectionStatus:
    """Build connection status representation safely without initiating network requests."""
    configured_mode, active_adapter, capabilities = get_adapter_info()
    mock_mode = bool(settings.UNIFI_MOCK_MODE)
    dry_run_blocks = bool(settings.UNIFI_DRY_RUN_BLOCKS)
    configured_site = str(settings.UNIFI_SITE or "default")

    if db is None:
        return UniFiConnectionStatus(
            configured_mode=configured_mode,
            active_adapter=active_adapter,
            configured_site=configured_site,
            connection_state="unknown",
            last_scan_at=None,
            last_scan_result=None,
            last_successful_scan_at=None,
            last_error=None,
            capabilities=capabilities,
            mock_mode=mock_mode,
            dry_run_blocks=dry_run_blocks,
        )

    # Retrieve the latest scan event
    latest_event = (
        db.query(Event)
        .filter(Event.event_type.in_(["scan_finished", "scan_failed"]))
        .order_by(Event.created_at.desc())
        .first()
    )

    if latest_event is None:
        return UniFiConnectionStatus(
            configured_mode=configured_mode,
            active_adapter=active_adapter,
            configured_site=configured_site,
            connection_state="unknown",
            last_scan_at=None,
            last_scan_result=None,
            last_successful_scan_at=None,
            last_error=None,
            capabilities=capabilities,
            mock_mode=mock_mode,
            dry_run_blocks=dry_run_blocks,
        )

    last_scan_at = latest_event.created_at
    if latest_event.event_type == "scan_finished":
        # Stale scan detection based on effective scan interval (including stored DB overrides)
        from app.db import utcnow
        from app.web.display import resolve_scan_interval

        now = utcnow()
        scan_time = (
            last_scan_at
            if last_scan_at.tzinfo is not None
            else last_scan_at.replace(tzinfo=now.tzinfo)
        )
        elapsed_seconds = (now - scan_time).total_seconds()

        interval_seconds, _ = resolve_scan_interval(db)
        stale_threshold = max(interval_seconds * 2, interval_seconds + 60)
        is_stale = elapsed_seconds > stale_threshold

        return UniFiConnectionStatus(
            configured_mode=configured_mode,
            active_adapter=active_adapter,
            configured_site=configured_site,
            connection_state="degraded" if is_stale else "successful",
            last_scan_at=last_scan_at,
            last_scan_result=True,
            last_successful_scan_at=last_scan_at,
            last_error=None,
            capabilities=capabilities,
            mock_mode=mock_mode,
            dry_run_blocks=dry_run_blocks,
        )

    # Scan failed: retrieve latest scan_finished independently
    last_error = sanitize_unifi_error(latest_event.message)
    latest_success = (
        db.query(Event)
        .filter(Event.event_type == "scan_finished")
        .order_by(Event.created_at.desc())
        .first()
    )
    last_successful_scan_at = latest_success.created_at if latest_success else None

    return UniFiConnectionStatus(
        configured_mode=configured_mode,
        active_adapter=active_adapter,
        configured_site=configured_site,
        connection_state="failed",
        last_scan_at=last_scan_at,
        last_scan_result=False,
        last_successful_scan_at=last_successful_scan_at,
        last_error=last_error,
        capabilities=capabilities,
        mock_mode=mock_mode,
        dry_run_blocks=dry_run_blocks,
    )
