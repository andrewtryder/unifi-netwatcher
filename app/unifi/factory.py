from __future__ import annotations

import logging

from app.unifi.base import UniFiAdapter
from app.unifi.legacy import LegacyUniFiAdapter

logger = logging.getLogger(__name__)

_shared_adapter: UniFiAdapter | None = None


def get_unifi_adapter() -> UniFiAdapter:
    """Retrieve or initialize the active UniFi adapter singleton."""
    global _shared_adapter
    if _shared_adapter is None:
        # Default to LegacyUniFiAdapter in Phase 1
        _shared_adapter = LegacyUniFiAdapter()
    return _shared_adapter


def close_unifi_adapter() -> None:
    """Close the active UniFi adapter singleton and release connection resources."""
    global _shared_adapter
    if _shared_adapter is not None:
        _shared_adapter.close()
        _shared_adapter = None
