"""Validate outbound webhook destinations against SSRF controls."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.config import settings

ALLOWED_HEADER_NAMES = frozenset({"authorization", "content-type", "user-agent"})


def _parse_allowed_hosts() -> set[str]:
    raw = settings.WEBHOOK_ALLOWED_HOSTS or ""
    return {h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()}


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def hostname_resolves_to_public(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if _is_blocked_ip(ip):
            return False
    return True


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """Return (ok, error_message). Enforces HTTPS, allowlist, and public DNS."""
    if not url or not isinstance(url, str):
        return False, "Webhook URL is required"
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https":
        return False, "Webhook URL must use HTTPS"
    if parsed.username or parsed.password:
        return False, "Webhook URL must not include credentials"
    if parsed.port not in (None, 443):
        return False, "Webhook URL must use port 443"
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False, "Webhook URL host is required"

    allowed = _parse_allowed_hosts()
    if not allowed:
        return False, "WEBHOOK_ALLOWED_HOSTS is not configured"
    if host not in allowed:
        return False, f"Host '{host}' is not in WEBHOOK_ALLOWED_HOSTS"

    # Reject IP literals that are non-public up front
    try:
        literal = ipaddress.ip_address(host)
        if _is_blocked_ip(literal):
            return False, "Webhook host resolves to a blocked address"
        return True, ""
    except ValueError:
        pass

    if not hostname_resolves_to_public(host):
        return False, "Webhook host resolves to a blocked or invalid address"
    return True, ""


def sanitize_webhook_headers(headers: dict | None) -> dict[str, str]:
    if not headers:
        return {}
    clean: dict[str, str] = {}
    for key, value in headers.items():
        name = str(key).strip()
        if name.lower() not in ALLOWED_HEADER_NAMES:
            continue
        clean[name] = str(value)[:1024]
    return clean
