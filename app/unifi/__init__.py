from __future__ import annotations

from app.unifi.base import UniFiAdapter
from app.unifi.client import UnifiClient, close_unifi_client, get_unifi_client
from app.unifi.factory import close_unifi_adapter, create_unifi_adapter, get_unifi_adapter
from app.unifi.legacy import LegacyUniFiAdapter
from app.unifi.models import (
    UniFiAuthError,
    UniFiCapabilities,
    UniFiClientDevice,
    UniFiConnectionError,
    UniFiError,
    UniFiPermissionError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from app.unifi.official import OfficialUniFiAdapter

__all__ = [
    "LegacyUniFiAdapter",
    "OfficialUniFiAdapter",
    "UniFiAdapter",
    "UniFiAuthError",
    "UniFiCapabilities",
    "UniFiClientDevice",
    "UniFiConnectionError",
    "UniFiError",
    "UniFiPermissionError",
    "UniFiResponseError",
    "UniFiTimeoutError",
    "UnifiClient",
    "close_unifi_adapter",
    "close_unifi_client",
    "create_unifi_adapter",
    "get_unifi_adapter",
    "get_unifi_client",
]
