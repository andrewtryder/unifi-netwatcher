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
    max_pages: int = 1000
    capabilities = UniFiCapabilities(
        can_block=False,
        can_unblock=False,
        has_wifi_telemetry=False,
        has_switch_port=False,
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
        if self._owns_client and self.client is not None:
            is_closed = getattr(self.client, "is_closed", False)
            if isinstance(is_closed, bool) and is_closed:
                return
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
            all_sites: list[dict[str, Any]] = []
            site_offset = 0
            site_limit = 100
            max_site_pages = min(self.max_pages, 100)
            candidate_not_found = False

            try:
                for _ in range(max_site_pages):
                    params = {"offset": site_offset, "limit": site_limit}
                    r = self.client.get(sites_url, headers=headers, params=params)
                    if r.status_code == 401:
                        raise UniFiAuthError(
                            "UniFi official API key invalid or unauthorized (HTTP 401)."
                        )
                    if r.status_code == 403:
                        raise UniFiPermissionError(
                            "UniFi official API key lacks required permissions (HTTP 403)."
                        )
                    if r.status_code == 404:
                        candidate_not_found = True
                        break
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

                    sites_page = payload["data"]
                    if not isinstance(sites_page, list):
                        raise UniFiResponseError("UniFi sites response 'data' field is not a list")

                    for s in sites_page:
                        if not isinstance(s, dict):
                            raise UniFiResponseError(
                                f"Malformed site record in UniFi sites response: expected dict, got {type(s).__name__}"
                            )
                        all_sites.append(s)

                    resp_offset = as_int(payload.get("offset"))
                    if resp_offset is not None and resp_offset != site_offset:
                        raise UniFiResponseError(
                            f"Inconsistent sites pagination offset: requested {site_offset}, got {resp_offset}"
                        )

                    resp_limit = as_int(payload.get("limit")) or site_limit
                    if resp_limit <= 0:
                        raise UniFiResponseError(f"Invalid sites pagination limit: {resp_limit}")

                    total_count = (
                        as_int(payload.get("totalCount"))
                        if payload.get("totalCount") is not None
                        else as_int(payload.get("total_count"))
                    )
                    if total_count is not None and total_count < 0:
                        raise UniFiResponseError(
                            f"Invalid negative totalCount in sites pagination: {total_count}"
                        )

                    if not sites_page:
                        if total_count is not None and site_offset < total_count:
                            raise UniFiResponseError(
                                f"Premature empty page in sites pagination: offset {site_offset} < totalCount {total_count}"
                            )
                        break

                    if (
                        total_count is not None
                        and len(sites_page) < resp_limit
                        and (site_offset + len(sites_page)) < total_count
                    ):
                        raise UniFiResponseError(
                            f"Incomplete sites pagination response: received {len(sites_page)} items with limit {resp_limit}, "
                            f"but offset {site_offset + len(sites_page)} is less than totalCount {total_count}"
                        )

                    site_offset += len(sites_page)

                    if total_count is not None:
                        if site_offset == total_count:
                            break
                        if site_offset > total_count:
                            raise UniFiResponseError(
                                f"Sites pagination overflow: received {site_offset} items, exceeding totalCount {total_count}"
                            )
                    elif len(sites_page) < resp_limit:
                        break
                else:
                    raise UniFiResponseError(
                        f"Maximum sites pagination pages ({max_site_pages}) exceeded while querying UniFi sites."
                    )

                if candidate_not_found:
                    continue

                # Match configured UNIFI_SITE using exact-match precedence
                configured = self.site.strip()
                matched_id: str | None = None

                # Tier 1: Exact ID match (UUID)
                tier1 = [s for s in all_sites if str(s.get("id", "")) == configured]
                if len(tier1) == 1:
                    matched_id = str(tier1[0].get("id", ""))
                elif len(tier1) > 1:
                    matched_ids = [s.get("id") for s in tier1]
                    raise UniFiResponseError(
                        f"Ambiguous UniFi site match for '{configured}': multiple sites matched by ID {matched_ids}"
                    )

                # Tier 2: Exact internalReference match (e.g. 'default')
                if matched_id is None:
                    tier2 = [
                        s for s in all_sites if str(s.get("internalReference", "")) == configured
                    ]
                    if len(tier2) == 1:
                        matched_id = str(tier2[0].get("id", ""))
                    elif len(tier2) > 1:
                        matched_ids = [s.get("id") for s in tier2]
                        raise UniFiResponseError(
                            f"Ambiguous UniFi site match for '{configured}': multiple sites matched by internalReference {matched_ids}"
                        )

                # Tier 3: Exact case-sensitive site name match
                if matched_id is None:
                    tier3 = [s for s in all_sites if str(s.get("name", "")) == configured]
                    if len(tier3) == 1:
                        matched_id = str(tier3[0].get("id", ""))
                    elif len(tier3) > 1:
                        matched_ids = [s.get("id") for s in tier3]
                        raise UniFiResponseError(
                            f"Ambiguous UniFi site match for '{configured}': multiple sites matched by name {matched_ids}"
                        )

                # Tier 4: Case-insensitive site name match
                if matched_id is None:
                    tier4 = [
                        s for s in all_sites if str(s.get("name", "")).lower() == configured.lower()
                    ]
                    if len(tier4) == 1:
                        matched_id = str(tier4[0].get("id", ""))
                    elif len(tier4) > 1:
                        matched_ids = [s.get("id") for s in tier4]
                        raise UniFiResponseError(
                            f"Ambiguous UniFi site match for '{configured}': multiple sites matched by case-insensitive name {matched_ids}"
                        )

                # Fallback: single site on console and configured is 'default'
                if matched_id is None and len(all_sites) == 1 and configured.lower() == "default":
                    matched_id = str(all_sites[0].get("id", ""))

                if matched_id is None:
                    available = [
                        s.get("name") or s.get("internalReference") or s.get("id")
                        for s in all_sites
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
        max_pages = self.max_pages

        for _ in range(max_pages):
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

                # Validate pagination metadata
                resp_offset = as_int(payload.get("offset"))
                if resp_offset is not None and resp_offset != offset:
                    raise UniFiResponseError(
                        f"Inconsistent pagination offset: requested {offset}, got {resp_offset}"
                    )

                resp_limit = as_int(payload.get("limit")) or limit
                if resp_limit <= 0:
                    raise UniFiResponseError(f"Invalid pagination limit: {resp_limit}")

                total_count = (
                    as_int(payload.get("totalCount"))
                    if payload.get("totalCount") is not None
                    else as_int(payload.get("total_count"))
                )
                if total_count is not None and total_count < 0:
                    raise UniFiResponseError(
                        f"Invalid negative totalCount in pagination: {total_count}"
                    )

                # Filter and normalize physical clients with valid MAC
                for item in data:
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
                            "Skipping client %s (type=%s, name=%s): invalid hardware address format",
                            item.get("id"),
                            item.get("type"),
                            item.get("name"),
                        )
                        continue

                    if mac in seen_macs:
                        logger.debug(
                            "Skipping duplicate client %s on site %s",
                            item.get("id"),
                            site_id,
                        )
                        continue

                    seen_macs.add(mac)
                    all_clients.append(UniFiClientDevice.from_official_dict(item, site_id=site_id))

                # Pagination completion validation based on raw response records and metadata
                if not data:
                    if total_count is not None and offset < total_count:
                        raise UniFiResponseError(
                            f"Premature empty page in pagination: offset {offset} < totalCount {total_count}"
                        )
                    break

                if (
                    total_count is not None
                    and len(data) < resp_limit
                    and (offset + len(data)) < total_count
                ):
                    raise UniFiResponseError(
                        f"Incomplete pagination response: received {len(data)} items with limit {resp_limit}, "
                        f"but offset {offset + len(data)} is less than totalCount {total_count}"
                    )

                offset += len(data)

                if total_count is not None:
                    if offset == total_count:
                        break
                    if offset > total_count:
                        raise UniFiResponseError(
                            f"Pagination overflow: received {offset} raw items, exceeding totalCount {total_count}"
                        )
                elif len(data) < resp_limit:
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
        else:
            raise UniFiResponseError(
                f"Maximum pagination pages ({max_pages}) exceeded while querying UniFi clients."
            )

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

    def probe(self) -> None:
        """Probe official API, validating connectivity, authentication, and permissions.

        Raises:
            UniFiAuthError: Invalid API key or unauthorized (401).
            UniFiPermissionError: Insufficient permissions (403).
            UniFiConnectionError: Host unreachable or connection failed.
            UniFiTimeoutError: Request timed out.
            UniFiResponseError: Malformed response or site resolution failed.
            UniFiError: Generic UniFi adapter failure.
        """
        self._load_settings()
        if self.mock_mode:
            return
        if not self.api_key.strip():
            raise UniFiAuthError(
                "UNIFI_API_KEY is not configured. Set UNIFI_API_KEY to use official API mode."
            )
        self._resolve_site()
        self.get_clients()

    def test_connection(self) -> tuple[bool, str]:
        """Test connection and authentication to the official UniFi API."""
        self._load_settings()
        if self.mock_mode:
            return True, "Mock mode active (official connection simulated)"

        if not self.api_key.strip():
            return False, "UNIFI_API_KEY is not configured"

        try:
            self.probe()
            return (
                True,
                f"Successfully connected to UniFi official API ({self.base_url}, site: {self.site} -> {self._resolved_site_id})",
            )
        except UniFiError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Connection failed: {exc}"
