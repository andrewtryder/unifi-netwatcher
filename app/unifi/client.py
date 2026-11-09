from __future__ import annotations

import httpx

from app.unifi.base import UniFiAdapter
from app.unifi.factory import close_unifi_adapter, get_unifi_adapter
from app.unifi.legacy import LegacyUniFiAdapter


class UnifiClient(LegacyUniFiAdapter):
    """Backward-compatible client subclassing LegacyUniFiAdapter."""

    def __init__(self, *, shared_http: httpx.Client | None = None) -> None:
        super().__init__(shared_http=shared_http)


def get_unifi_client() -> UniFiAdapter:
    """Process-scoped UniFi client delegating to the unified adapter factory."""
    return get_unifi_adapter()


def close_unifi_client() -> None:
    """Close the shared process-scoped UniFi client via the adapter factory."""
    close_unifi_adapter()
