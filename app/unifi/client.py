from __future__ import annotations

import httpx

from app.config import settings
from app.unifi.legacy import LegacyUniFiAdapter


class UnifiClient(LegacyUniFiAdapter):
    """Backward-compatible client subclassing LegacyUniFiAdapter."""

    def __init__(self, *, shared_http: httpx.Client | None = None) -> None:
        super().__init__(shared_http=shared_http)


_shared_client: UnifiClient | None = None


def get_unifi_client() -> UnifiClient:
    """Process-scoped UniFi client with a reused httpx connection pool."""
    global _shared_client
    if _shared_client is None:
        _shared_client = UnifiClient()
    else:
        # Refresh settings that may change via env in tests
        _shared_client.mock_mode = settings.UNIFI_MOCK_MODE
        _shared_client.dry_run_blocks = settings.UNIFI_DRY_RUN_BLOCKS
        _shared_client.base_url = settings.UNIFI_URL.rstrip("/")
        _shared_client.site = settings.UNIFI_SITE
        _shared_client.username = settings.UNIFI_USERNAME
        _shared_client.password = settings.unifi_password()
        _shared_client.verify_ssl = settings.unifi_verify()
    return _shared_client


def close_unifi_client() -> None:
    """Close the shared process-scoped UniFi client."""
    global _shared_client
    if _shared_client is not None:
        _shared_client.close()
        _shared_client = None
