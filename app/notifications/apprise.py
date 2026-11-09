from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

import apprise

from app.notifications.base import NotificationProvider
from app.notifications.ssrf import (
    WebhookURLError,
    _is_always_blocked_ip,
    _is_private_lan_ip,
    _resolve_allowed_ips,
)

logger = logging.getLogger(__name__)


def extract_apprise_target(url: str) -> tuple[str | None, int | None]:
    """Extract host and port target from an Apprise URL for SSRF validation."""
    if not url or not isinstance(url, str):
        return None, None
    clean = url.strip()
    if clean.lower().startswith(("file:", "file/")):
        return None, None
    try:
        ap = apprise.Apprise()
        if not ap.add(clean):
            return None, None
        if not ap:
            return None, None
        server = ap[0]
        # 1. Custom/Fixed notify_url (e.g. Discord, Telegram, Slack)
        notify_url = getattr(server, "notify_url", None)
        if isinstance(notify_url, str) and notify_url.startswith(("http://", "https://")):
            parsed = urlparse(notify_url)
            return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

        # 2. Check if Ntfy mode is CLOUD
        ntfy_mode = getattr(getattr(apprise.plugins, "ntfy", None), "NtfyMode", None)
        if ntfy_mode and getattr(server, "mode", None) == ntfy_mode.CLOUD:
            cloud_url = getattr(server, "cloud_notify_url", "https://ntfy.sh")
            parsed = urlparse(cloud_url)
            return parsed.hostname, parsed.port or 443

        # 3. Check server.host
        host = getattr(server, "host", None)
        port = getattr(server, "port", None)
        if host:
            return str(host), port or (443 if getattr(server, "secure", False) else 80)

        parsed = urlparse(clean)
        return parsed.hostname, parsed.port
    except Exception as exc:
        logger.debug("Failed to extract Apprise target: %s", exc)
        return None, None


def validate_apprise_target_ssrf(
    host: str, port: int = 443, *, allow_private_network: bool = False
) -> bool:
    """Validate extracted Apprise target host against SSRF rules."""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
        if _is_always_blocked_ip(ip):
            return False
        if _is_private_lan_ip(ip) and not allow_private_network:
            return False
        return True
    except ValueError:
        pass

    try:
        _resolve_allowed_ips(host, port, allow_private_network=allow_private_network)
        return True
    except WebhookURLError:
        return False


class AppriseProvider(NotificationProvider):
    type = "apprise"

    def validate_config(self, config: dict[str, Any]) -> bool:
        if not isinstance(config, dict):
            return False
        url = config.get("url")
        if not url or not isinstance(url, str):
            return False
        allow_private = bool(config.get("allow_private_network", False))
        host, port = extract_apprise_target(url)
        if not host:
            return False
        return validate_apprise_target_ssrf(host, port or 443, allow_private_network=allow_private)

    def send(self, message: str, config: dict[str, Any]) -> tuple[bool, int, str, str]:
        url = config.get("url")
        if not url or not isinstance(url, str):
            return False, 0, "", "Missing Apprise URL"
        allow_private = bool(config.get("allow_private_network", False))
        host, port = extract_apprise_target(url)
        if not host:
            return False, 0, "", "Invalid Apprise URL or unsupported notification scheme"
        if not validate_apprise_target_ssrf(host, port or 443, allow_private_network=allow_private):
            return False, 0, "", "Apprise destination blocked by network security policy"

        errors: list[str] = []

        def _log_callback(entry: Any, _notify_base: Any) -> None:
            if getattr(entry, "level", 0) >= logging.WARNING:
                msg = getattr(entry, "message", None)
                if msg:
                    errors.append(str(msg))

        try:
            ap = apprise.Apprise(log_callback=_log_callback)
            if not ap.add(url):
                return False, 0, "", "Failed to initialize Apprise destination URL"
            success = ap.notify(body=message, title="NetWatcher Alert")
            if success:
                return True, 200, "Delivered via Apprise", ""
            error_str = "; ".join(errors) if errors else "Apprise notification failed"
            return False, 500, "", error_str
        except Exception as exc:
            logger.error("Apprise notification failed with exception: %s", exc)
            return False, 0, "", str(exc)
