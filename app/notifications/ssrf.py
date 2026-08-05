from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import settings


class WebhookURLError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedWebhookTarget:
    """Validated destination: connect to ``connect_host`` with TLS/HTTP Host ``hostname``."""

    original_url: str
    hostname: str
    port: int
    connect_host: str  # literal IP preferred for hostnames
    allow_private_network: bool


# Explicit cloud metadata addresses (also covered by link-local for IPv4).
_METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)


def _is_metadata_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(addr in net for net in _METADATA_NETWORKS)


def _is_always_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Loopback, link-local, metadata, multicast, reserved, unspecified — never allowed."""
    return bool(
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or _is_metadata_ip(addr)
    )


def _is_private_lan_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """RFC1918 / ULA private addresses (excluding always-blocked categories)."""
    if _is_always_blocked_ip(addr):
        return False
    return bool(addr.is_private)


def _address_blocked(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private_network: bool,
) -> bool:
    if _is_always_blocked_ip(addr):
        return True
    if _is_private_lan_ip(addr) and not allow_private_network:
        return True
    return False


def _resolve_allowed_ips(
    host: str, port: int, *, allow_private_network: bool
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebhookURLError(f"Unable to resolve webhook host: {host}") from exc

    if not infos:
        raise WebhookURLError(f"Unable to resolve webhook host: {host}")

    allowed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        try:
            resolved = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _address_blocked(resolved, allow_private_network=allow_private_network):
            raise WebhookURLError("Webhook URL resolves to a blocked or private address")
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            allowed.append(resolved)
    if not allowed:
        raise WebhookURLError(f"Unable to resolve webhook host: {host}")
    return allowed


def resolve_webhook_target(
    url: str, *, allow_private_network: bool = False
) -> ResolvedWebhookTarget:
    """Validate URL and return a pin-to-IP connect target (closes DNS TOCTOU on connect)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise WebhookURLError("Webhook URL must use HTTPS")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise WebhookURLError("Webhook URL host is required")

    host_allowlisted = host in settings.webhook_allowed_hosts
    effective_allow_private = allow_private_network or host_allowlisted
    port = parsed.port or 443

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        if _address_blocked(ip, allow_private_network=effective_allow_private):
            raise WebhookURLError("Webhook URL must not target blocked or private addresses")
        return ResolvedWebhookTarget(
            original_url=url,
            hostname=host,
            port=port,
            connect_host=host,
            allow_private_network=effective_allow_private,
        )

    allowed = _resolve_allowed_ips(host, port, allow_private_network=effective_allow_private)
    # Prefer IPv4 for broader httpx/SNI compatibility when both exist.
    chosen = next((a for a in allowed if isinstance(a, ipaddress.IPv4Address)), allowed[0])
    return ResolvedWebhookTarget(
        original_url=url,
        hostname=host,
        port=port,
        connect_host=str(chosen),
        allow_private_network=effective_allow_private,
    )


def validate_webhook_url(url: str, *, allow_private_network: bool = False) -> str:
    """Reject non-HTTPS and blocked destinations; resolve and pin checks run via resolve."""
    resolve_webhook_target(url, allow_private_network=allow_private_network)
    return url


def pinned_request_url(target: ResolvedWebhookTarget, *, original_url: str) -> str:
    """HTTPS URL using the validated IP + original path/query; set Host/SNI separately."""
    parsed = urlparse(original_url)
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    host = target.connect_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"https://{host}:{target.port}{path}{query}"
