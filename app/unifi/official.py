from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.mac import normalize_mac
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
    as_int,
)

logger = logging.getLogger(__name__)


class OfficialUniFiAdapter(UniFiAdapter):
    """Adapter for modern UniFi Network Integration API (v1) using X-API-KEY authentication."""

    adapter_name = "official"
    capabilities = UniFiCapabilities(
        can_block=False,
        can_unblock=False,
        has_wifi_telemetry=True,
        has_switch_port=True,
        supports_pagination=True,
    )

    def __init__(self, *, shared_http: httpx.Client | None = None) -> None:
        self._load_settings()
        self._owns_client = shared_http is None
        self.client = shared_http or httpx.Client(verify=self.verify_ssl, timeout=self.timeout)
        self._resolved_site_id: str | None = None
        self._api_base_path: str | None = None

    def _load_settings(self) -> None:
        self.base_url = settings.UNIFI_URL.rstrip("/")
        self.site = settings.UNIFI_SITE
        self.api_key = settings.unifi_api_key()
        self.verify_ssl = settings.unifi_verify()
        self.timeout = settings.UNIFI_TIMEOUT_SECONDS
        self.mock_mode = settings.UNIFI_MOCK_MODE

    def close(self) -> None:
        if self._owns_client and self.client is not None and not self.client.is_closed:
            self.client.close()

    def _get_headers(self) -> dict[str, str]:
        return {
            "X-API-KEY": self.api_key,
            "Accept": "application/json",
        }

    def _get_mock_data(self) -> dict[str, Any]:
        mock_file = Path(__file__).parent / "mock_unifi_official_data.json"
        try:
            with open(mock_file) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load official mock data: {e}")
            return {"sites": {"data": []}, "clients": {"data": []}}

    def _candidate_base_paths(self) -> list[str]:
        if "/proxy/network" in self.base_url:
            return ["/integration/v1", "/v1"]
        return ["/proxy/network/integration/v1", "/integration/v1", "/v1"]

    def _resolve_site(self) -> str:
        """Resolve the configured UNIFI_SITE to the official API site UUID."""
        if self._resolved_site_id is not None and self._api_base_path is not None:
            return self._resolved_site_id

        self._load_settings()
        if self.mock_mode:
            self._resolved_site_id = "mock-official-site-uuid-1"
            self._api_base_path = f"{self.base_url}/proxy/network/integration/v1"
            return self._resolved_site_id

        if not self.api_key.strip():
            raise UniFiAuthError(
                "UNIFI_API_KEY is not configured. Set UNIFI_API_KEY to use official API mode."
            )

        headers = self._get_headers()
        last_error: Exception | None = None

        for candidate in self._candidate_base_paths():
            candidate_base = f"{self.base_url}{candidate}"
            sites_url = f"{candidate_base}/sites"
            try:
                r = self.client.get(sites_url, headers=headers)
                if r.status_code == 401:
                    raise UniFiAuthError(
                        "UniFi official API key invalid or unauthorized (HTTP 401)."
                    )
                if r.status_code == 403:
                    raise UniFiPermissionError(
                        "UniFi official API key lacks required permissions (HTTP 403)."
                    )
                if r.status_code == 404:
                    # Endpoint path not found at this candidate; try next candidate
                    continue
                r.raise_for_status()

                try:
                    payload = r.json()
                except Exception as exc:
                    raise UniFiResponseError(
                        f"Malformed JSON from UniFi sites endpoint: {exc}"
                    ) from exc

                if not isinstance(payload, dict) or "data" not in payload:
                    raise UniFiResponseError(
                        "Unexpected response format from UniFi sites endpoint (missing 'data')"
                    )

                sites = payload["data"]
                if not isinstance(sites, list):
                    raise UniFiResponseError("UniFi sites response 'data' field is not a list")

                for s in sites:
                    if not isinstance(s, dict):
                        raise UniFiResponseError(
                            f"Malformed site record in UniFi sites response: expected dict, got {type(s).__name__}"
                        )

                # Match configured UNIFI_SITE
                configured = self.site.strip()
                matching_sites: list[dict[str, Any]] = []
                for site_obj in sites:
                    site_id = str(site_obj.get("id", ""))
                    site_name = str(site_obj.get("name", ""))
                    internal_ref = str(site_obj.get("internalReference", ""))

                    if (
                        internal_ref == configured
                        or site_name.lower() == configured.lower()
                        or site_id == configured
                    ):
                        matching_sites.append(site_obj)

                if len(matching_sites) > 1:
                    matched_ids = [s.get("id") for s in matching_sites]
                    raise UniFiResponseError(
                        f"Ambiguous UniFi site match for '{configured}': multiple sites matched {matched_ids}"
                    )

                matched_id: str | None = None
                if len(matching_sites) == 1:
                    matched_id = str(matching_sites[0].get("id", ""))
                elif len(sites) == 1 and configured.lower() == "default":
                    matched_id = str(sites[0].get("id", ""))

                if matched_id is None:
                    available = [
                        s.get("name") or s.get("internalReference") or s.get("id")
                        for s in sites
                        if isinstance(s, dict)
                    ]
                    raise UniFiResponseError(
                        f"Configured UniFi site '{configured}' not found in official API sites: {available}"
                    )

                self._api_base_path = candidate_base
                self._resolved_site_id = matched_id
                return self._resolved_site_id

            except UniFiAuthError, UniFiPermissionError, UniFiResponseError:
                raise
            except (httpx.ConnectError, httpx.NetworkError) as e:
                raise UniFiConnectionError(
                    f"Cannot reach UniFi controller at {self.base_url}: {e}"
                ) from e
            except httpx.TimeoutException as e:
                raise UniFiTimeoutError(
                    f"UniFi controller request timed out after {self.timeout}s: {e}"
                ) from e
            except Exception as e:
                last_error = e

        if last_error:
            raise UniFiError(
                f"Failed to communicate with official UniFi API: {last_error}"
            ) from last_error
        raise UniFiResponseError(
            f"Official UniFi Integration API endpoint not found on {self.base_url}. Checked candidates: {self._candidate_base_paths()}"
        )

    def get_clients(self) -> list[UniFiClientDevice]:
        """Fetch and normalize active client devices using official paginated API."""
        self._load_settings()
        if self.mock_mode:
            logger.info("Official UniFi Adapter running in MOCK MODE. Returning mock clients.")
            mock_data = self._get_mock_data()
            raw_clients = mock_data.get("clients", {}).get("data", [])
            return [
                UniFiClientDevice.from_official_dict(c, site_id="mock-official-site-uuid-1")
                for c in raw_clients
                if isinstance(c, dict)
            ]

        site_id = self._resolve_site()
        base_path = self._api_base_path
        if not base_path:
            raise UniFiResponseError("Official API base path is unresolved.")

        clients_url = f"{base_path}/sites/{site_id}/clients"
        headers = self._get_headers()
        seen_macs: set[str] = set()
        all_clients: list[UniFiClientDevice] = []
        offset = 0
        limit = 100
        max_pages = 1000

        for _ in range(max_pages):
            previous_offset = offset
            params = {"offset": offset, "limit": limit}
            try:
                r = self.client.get(clients_url, headers=headers, params=params)
                if r.status_code == 401:
                    raise UniFiAuthError("UniFi official API key unauthorized (HTTP 401).")
                if r.status_code == 403:
                    raise UniFiPermissionError(
                        "UniFi official API key lacks required permissions (HTTP 403)."
                    )
                if r.status_code == 404:
                    self._resolved_site_id = None
                    raise UniFiResponseError("UniFi clients endpoint not found (HTTP 404).")
                r.raise_for_status()

                try:
                    payload = r.json()
                except Exception as exc:
                    raise UniFiResponseError(
                        f"Malformed JSON from UniFi clients endpoint: {exc}"
                    ) from exc

                if not isinstance(payload, dict):
                    raise UniFiResponseError(
                        "Unexpected response format from UniFi clients endpoint (expected JSON object)"
                    )

                if "data" not in payload:
                    raise UniFiResponseError("UniFi clients response missing required 'data' field")

                data = payload["data"]
                if not isinstance(data, list):
                    raise UniFiResponseError(
                        f"UniFi clients response 'data' field is not a list (got {type(data).__name__})"
                    )

                for idx, item in enumerate(data):
                    if not isinstance(item, dict):
                        raise UniFiResponseError(
                            f"Malformed client record at index {idx}: expected dict, got {type(item).__name__}"
                        )
                    raw_mac = item.get("macAddress") or item.get("mac")
                    if not raw_mac or not isinstance(raw_mac, str) or not raw_mac.strip():
                        logger.debug(
                            "Skipping client %s (type=%s, name=%s): no MAC address provided",
                            item.get("id"),
                            item.get("type"),
                            item.get("name"),
                        )
                        continue

                    mac = normalize_mac(raw_mac)
                    if not mac:
                        logger.debug(
                            "Skipping client %s (type=%s, name=%s): invalid MAC address %r",
                            item.get("id"),
                            item.get("type"),
                            item.get("name"),
                            raw_mac,
                        )
                        continue

                    if mac in seen_macs:
                        logger.debug("Skipping duplicate client MAC %s on site %s", mac, site_id)
                        continue

                    seen_macs.add(mac)
                    all_clients.append(UniFiClientDevice.from_official_dict(item, site_id=site_id))

                # Pagination termination checks
                total_count = as_int(payload.get("totalCount"))
                resp_limit = as_int(payload.get("limit")) or limit

                if not data:
                    break
                if total_count is not None and len(all_clients) >= total_count:
                    break
                if total_count is None and len(data) < resp_limit:
                    break

                offset += len(data)
                if offset == previous_offset:
                    break

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
                raise UniFiError(
                    f"Unexpected error communicating with UniFi official API: {e}"
                ) from e

        return all_clients

    def block_client(self, mac: str) -> bool:
        logger.warning(
            "Client blocking is not supported by the official UniFi Network integration API. "
            "To enable client blocking, use legacy API mode."
        )
        return False

    def unblock_client(self, mac: str) -> bool:
        logger.warning(
            "Client unblocking is not supported by the official UniFi Network integration API. "
            "To enable client unblocking, use legacy API mode."
        )
        return False

    def test_connection(self) -> tuple[bool, str]:
        """Test connection and authentication to the official UniFi API."""
        self._load_settings()
        if self.mock_mode:
            return True, "Mock mode active (official connection simulated)"

        if not self.api_key.strip():
            return False, "UNIFI_API_KEY is not configured"

        try:
            site_id = self._resolve_site()
            self.get_clients()
            return (
                True,
                f"Successfully connected to UniFi official API ({self.base_url}, site: {self.site} -> {site_id})",
            )
        except UniFiError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Connection failed: {exc}"
