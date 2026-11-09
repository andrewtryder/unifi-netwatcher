from __future__ import annotations

import logging

from app.config import settings
from app.unifi.base import UniFiAdapter
from app.unifi.legacy import LegacyUniFiAdapter
from app.unifi.models import UniFiAuthError, UniFiPermissionError
from app.unifi.official import OfficialUniFiAdapter

logger = logging.getLogger(__name__)

_shared_adapter: UniFiAdapter | None = None


def create_unifi_adapter(mode: str | None = None) -> UniFiAdapter:
    """Instantiate a new UniFi adapter based on API mode configuration."""
    api_mode = (mode or settings.UNIFI_API_MODE).strip().lower()

    if api_mode == "official":
        logger.info("Initializing Official UniFi Adapter (integration API).")
        return OfficialUniFiAdapter()

    if api_mode == "auto":
        api_key = settings.unifi_api_key().strip()
        if api_key and not settings.UNIFI_MOCK_MODE:
            logger.info("UNIFI_API_MODE=auto: API key detected, probing official API...")
            adapter = OfficialUniFiAdapter()
            try:
                adapter.probe()
                logger.info("Official API probe successful; selected OfficialUniFiAdapter.")
                return adapter
            except UniFiAuthError, UniFiPermissionError:
                adapter.close()
                # Do not silently fall back on credential/permission errors
                raise
            except Exception as e:
                adapter.close()
                logger.warning(
                    f"Official API probe encountered error ({e}); falling back to legacy adapter."
                )
        elif settings.UNIFI_MOCK_MODE and api_key:
            return OfficialUniFiAdapter()
        else:
            logger.info("UNIFI_API_MODE=auto: no API key configured; selecting legacy adapter.")
        return LegacyUniFiAdapter()

    # Default to legacy mode
    logger.info("Initializing Legacy UniFi Adapter (session authentication).")
    return LegacyUniFiAdapter()


def get_unifi_adapter() -> UniFiAdapter:
    """Retrieve or initialize the active UniFi adapter singleton."""
    global _shared_adapter
    if _shared_adapter is None:
        _shared_adapter = create_unifi_adapter()
    return _shared_adapter


def close_unifi_adapter() -> None:
    """Close the active UniFi adapter singleton and release connection resources."""
    global _shared_adapter
    if _shared_adapter is not None:
        _shared_adapter.close()
        _shared_adapter = None
