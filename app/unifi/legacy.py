from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.unifi.base import UniFiAdapter
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

logger = logging.getLogger(__name__)


class LegacyUniFiAdapter(UniFiAdapter):
    """Adapter for classic session-cookie authenticated UniFi controllers."""

    adapter_name = "legacy"
    capabilities = UniFiCapabilities(
        can_block=True,
        can_unblock=True,
        has_wifi_telemetry=True,
        has_switch_port=True,
        supports_pagination=False,
    )

    def __init__(self, *, shared_http: httpx.Client | None = None) -> None:
        self._load_settings()
        self._owns_client = shared_http is None
        self.client = shared_http or httpx.Client(verify=self.verify_ssl, timeout=self.timeout)
        self._logged_in = False

    def _load_settings(self) -> None:
        self.base_url = settings.UNIFI_URL.rstrip("/")
        self.site = settings.UNIFI_SITE
        self.username = settings.UNIFI_USERNAME
        self.password = settings.unifi_password()
        self.verify_ssl = settings.unifi_verify()
        self.timeout = settings.UNIFI_TIMEOUT_SECONDS
        self.mock_mode = settings.UNIFI_MOCK_MODE
        self.dry_run_blocks = settings.UNIFI_DRY_RUN_BLOCKS

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

    def _authenticate(self) -> None:
        """Authenticate against the UniFi controller, raising typed UniFiError on failure."""
        self._load_settings()
        if self.mock_mode:
            logger.info("Legacy UniFi Adapter running in MOCK MODE. Simulating login.")
            self._logged_in = True
            return

        login_url = f"{self.base_url}/api/auth/login"
        payload = {"username": self.username, "password": self.password}
        try:
            r = self.client.post(login_url, json=payload)
            if r.status_code == 401:
                self._logged_in = False
                raise UniFiAuthError(
                    "Failed to login to UniFi Controller: Invalid credentials (401)"
                )
            if r.status_code == 403:
                self._logged_in = False
                raise UniFiPermissionError(
                    "Failed to login to UniFi Controller: Permission denied (403)"
                )
            r.raise_for_status()
            self._logged_in = True
        except UniFiAuthError, UniFiPermissionError:
            raise
        except (httpx.ConnectError, httpx.NetworkError) as e:
            self._logged_in = False
            raise UniFiConnectionError(
                f"Cannot reach UniFi controller at {self.base_url}: {e}"
            ) from e
        except httpx.TimeoutException as e:
            self._logged_in = False
            raise UniFiTimeoutError(
                f"UniFi controller login timed out after {self.timeout}s: {e}"
            ) from e
        except httpx.HTTPStatusError as e:
            self._logged_in = False
            if e.response.status_code == 401:
                raise UniFiAuthError(
                    f"UniFi authorization error (HTTP {e.response.status_code})"
                ) from e
            if e.response.status_code == 403:
                raise UniFiPermissionError(
                    f"UniFi access forbidden (HTTP {e.response.status_code})"
                ) from e
            raise UniFiResponseError(f"UniFi login HTTP error: {e.response.status_code}") from e
        except Exception as e:
            self._logged_in = False
            raise UniFiError(f"Unexpected error authenticating with UniFi controller: {e}") from e

    def login(self) -> bool:
        """Authenticate against the UniFi controller using session cookies.

        Preserves boolean return for backward compatibility with callers.
        """
        try:
            self._authenticate()
            return True
        except UniFiError as e:
            logger.error(f"UniFi login failed: {e}")
            return False

    def get_clients(self) -> list[UniFiClientDevice]:
        """Fetch and normalize active client devices from /stat/sta."""
        self._load_settings()
        if self.mock_mode:
            logger.info("Legacy UniFi Adapter running in MOCK MODE. Returning mock clients.")
            raw_clients = self._get_mock_data()
            return [UniFiClientDevice.from_legacy_dict(c) for c in raw_clients]

        if not self._logged_in:
            self._authenticate()

        url = f"{self.base_url}/proxy/network/api/s/{self.site}/stat/sta"
        try:
            r = self.client.get(url)
            if r.status_code == 401:
                logger.info("UniFi session expired, re-authenticating...")
                self._authenticate()
                r = self.client.get(url)
            if r.status_code == 401:
                raise UniFiAuthError("UniFi unauthorized (HTTP 401) after re-authentication.")
            if r.status_code == 403:
                raise UniFiPermissionError("UniFi permission denied (HTTP 403).")
            r.raise_for_status()

            try:
                payload = r.json()
            except Exception as exc:
                raise UniFiResponseError(f"Malformed JSON from UniFi controller: {exc}") from exc

            if not isinstance(payload, dict):
                raise UniFiResponseError(
                    "Unexpected response format from UniFi controller (expected JSON object)"
                )

            meta = payload.get("meta")
            if isinstance(meta, dict) and meta.get("rc") == "error":
                msg = meta.get("msg", "Unknown controller error")
                raise UniFiResponseError(f"UniFi controller returned error: {msg}")

            if "data" not in payload:
                raise UniFiResponseError("UniFi response missing required 'data' field")

            data = payload["data"]
            if not isinstance(data, list):
                raise UniFiResponseError(
                    f"UniFi response 'data' field is not a list (got {type(data).__name__})"
                )

            clients: list[UniFiClientDevice] = []
            for idx, item in enumerate(data):
                if not isinstance(item, dict):
                    raise UniFiResponseError(
                        f"Malformed client record at index {idx} in UniFi response: expected dict, got {type(item).__name__}"
                    )
                mac = item.get("mac")
                if not mac or not isinstance(mac, str) or not mac.strip():
                    raise UniFiResponseError(
                        f"Malformed client record at index {idx} in UniFi response: missing or invalid 'mac' identifier"
                    )
                clients.append(UniFiClientDevice.from_legacy_dict(item))

            return clients

        except UniFiError, UniFiAuthError, UniFiPermissionError, UniFiResponseError:
            raise
        except (httpx.ConnectError, httpx.NetworkError) as e:
            raise UniFiConnectionError(
                f"Cannot reach UniFi controller at {self.base_url}: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise UniFiTimeoutError(
                f"UniFi controller request timed out after {self.timeout}s: {e}"
            ) from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise UniFiAuthError(
                    f"UniFi authorization error (HTTP {e.response.status_code})"
                ) from e
            if e.response.status_code == 403:
                raise UniFiPermissionError(
                    f"UniFi access forbidden (HTTP {e.response.status_code})"
                ) from e
            raise UniFiResponseError(f"UniFi HTTP error: {e.response.status_code}") from e
        except Exception as e:
            raise UniFiError(f"Unexpected error communicating with UniFi controller: {e}") from e

    def _stamgr_cmd(self, cmd: str, mac: str) -> bool:
        self._load_settings()
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

    def test_connection(self) -> tuple[bool, str]:
        """Test connection to the UniFi controller."""
        self._load_settings()
        if self.mock_mode:
            return True, "Mock mode active (connection simulated)"
        try:
            if not self._logged_in:
                self._authenticate()
            # Verify client endpoint
            self.get_clients()
            return (
                True,
                f"Successfully connected to UniFi controller ({self.base_url}, site: {self.site})",
            )
        except UniFiError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Connection failed: {exc}"
