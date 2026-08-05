import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class UnifiClient:
    def __init__(self, *, shared_http: httpx.Client | None = None):
        self.base_url = settings.UNIFI_URL.rstrip("/")
        self.site = settings.UNIFI_SITE
        self.username = settings.UNIFI_USERNAME
        self.password = settings.UNIFI_PASSWORD
        self.verify_ssl = settings.UNIFI_VERIFY_SSL
        self.timeout = settings.UNIFI_TIMEOUT_SECONDS
        self.mock_mode = settings.UNIFI_MOCK_MODE
        self.dry_run_blocks = settings.UNIFI_DRY_RUN_BLOCKS
        self._owns_client = shared_http is None
        self.client = shared_http or httpx.Client(verify=self.verify_ssl, timeout=self.timeout)
        self._logged_in = False

    def close(self) -> None:
        if self._owns_client and self.client is not None and not self.client.is_closed:
            self.client.close()

    def _get_mock_data(self) -> list[dict[str, Any]]:
        mock_file = Path(__file__).parent / "mock_unifi_data.json"
        try:
            with open(mock_file) as f:
                data = json.load(f)
                return data.get("data", [])
        except Exception as e:
            logger.error(f"Failed to load mock data: {e}")
            return []

    def login(self) -> bool:
        if self.mock_mode:
            logger.info("UniFi Client running in MOCK MODE. Simulating login.")
            self._logged_in = True
            return True

        login_url = f"{self.base_url}/api/auth/login"
        payload = {"username": self.username, "password": self.password}
        try:
            r = self.client.post(login_url, json=payload)
            r.raise_for_status()
            self._logged_in = True
            return True
        except Exception as e:
            logger.error(f"Failed to login to UniFi Controller: {e}")
            return False

    def get_clients(self) -> list[dict[str, Any]]:
        if self.mock_mode:
            logger.info("UniFi Client running in MOCK MODE. Returning mock clients.")
            return self._get_mock_data()

        if not self._logged_in and not self.login():
            return []

        url = f"{self.base_url}/proxy/network/api/s/{self.site}/stat/sta"
        try:
            r = self.client.get(url)
            if r.status_code == 401:
                if self.login():
                    r = self.client.get(url)
            r.raise_for_status()
            return r.json().get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch clients: {e}")
            return []

    def _stamgr_cmd(self, cmd: str, mac: str) -> bool:
        if self.mock_mode or self.dry_run_blocks:
            logger.info(f"[DRY RUN/MOCK] Would execute {cmd} on {mac}")
            return True

        if not self._logged_in and not self.login():
            return False

        url = f"{self.base_url}/proxy/network/api/s/{self.site}/cmd/stamgr"
        payload = {"cmd": cmd, "mac": mac}
        try:
            r = self.client.post(url, json=payload)
            if r.status_code == 401:
                if self.login():
                    r = self.client.post(url, json=payload)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to execute stamgr {cmd} on {mac}: {e}")
            return False

    def block_client(self, mac: str) -> bool:
        return self._stamgr_cmd("block-sta", mac)

    def unblock_client(self, mac: str) -> bool:
        return self._stamgr_cmd("unblock-sta", mac)


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
        _shared_client.password = settings.UNIFI_PASSWORD
        _shared_client.verify_ssl = settings.UNIFI_VERIFY_SSL
    return _shared_client


def close_unifi_client() -> None:
    global _shared_client
    if _shared_client is not None:
        _shared_client.close()
        _shared_client = None
