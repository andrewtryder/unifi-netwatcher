from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from app.config import settings
from app.unifi import (
    LegacyUniFiAdapter,
    OfficialUniFiAdapter,
    UniFiAdapter,
    UniFiAuthError,
    UniFiClientDevice,
    UniFiConnectionError,
    UniFiPermissionError,
    UniFiResponseError,
    UniFiTimeoutError,
    close_unifi_adapter,
    create_unifi_adapter,
)
from pydantic import SecretStr


def test_official_adapter_capabilities():
    adapter = OfficialUniFiAdapter()
    assert adapter.adapter_name == "official"
    assert isinstance(adapter, UniFiAdapter)
    assert adapter.capabilities.can_block is False
    assert adapter.capabilities.can_unblock is False
    assert adapter.capabilities.supports_pagination is True
    assert adapter.capabilities.has_wifi_telemetry is False
    assert adapter.capabilities.has_switch_port is False

    # Blocking is not supported in official integration API
    assert adapter.block_client("00:11:22:33:44:55") is False
    assert adapter.unblock_client("00:11:22:33:44:55") is False


def test_unifi_client_device_from_official_dict():
    # Wired device
    wired_raw = {
        "id": "c-wired-1",
        "macAddress": "00:aa:bb:cc:dd:ee",
        "ipAddress": "192.168.1.50",
        "name": "Desktop-PC",
        "hostname": "desktop-pc",
        "type": "WIRED",
        "switchPort": 7,
    }
    dev = UniFiClientDevice.from_official_dict(wired_raw, site_id="site-123")
    assert dev.mac == "00:aa:bb:cc:dd:ee"
    assert dev.ip == "192.168.1.50"
    assert dev.name == "Desktop-PC"
    assert dev.hostname == "desktop-pc"
    assert dev.is_wired is True
    assert dev.sw_port == 7
    assert dev.site_id == "site-123"

    # Wireless device
    wireless_raw = {
        "id": "c-wifi-1",
        "macAddress": "11:22:33:44:55:66",
        "ipAddress": "192.168.1.60",
        "name": "Smartphone",
        "type": "WIRELESS",
        "ssid": "HomeNetwork",
        "accessPointMac": "aa:bb:cc:11:22:33",
        "rssi": -60,
        "txRateBps": 433000000,
        "rxRateBps": 433000000,
        "channel": 36,
    }
    dev_wifi = UniFiClientDevice.from_official_dict(wireless_raw)
    assert dev_wifi.mac == "11:22:33:44:55:66"
    assert dev_wifi.ip == "192.168.1.60"
    assert dev_wifi.name == "Smartphone"
    assert dev_wifi.hostname == "Smartphone"
    assert dev_wifi.is_wired is False
    assert dev_wifi.essid == "HomeNetwork"
    assert dev_wifi.ap_mac == "aa:bb:cc:11:22:33"
    assert dev_wifi.rssi == -60
    assert dev_wifi.tx_rate_bps == 433000000
    assert dev_wifi.channel == 36


def test_official_adapter_mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)
    adapter = OfficialUniFiAdapter()

    clients = adapter.get_clients()
    assert len(clients) == 4
    assert all(isinstance(c, UniFiClientDevice) for c in clients)
    assert any(c.name == "work-laptop" for c in clients)
    assert any(c.is_wired is True for c in clients)

    success, msg = adapter.test_connection()
    assert success is True
    assert "Mock mode" in msg


def test_official_adapter_missing_api_key(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr(""))
    adapter = OfficialUniFiAdapter()

    with pytest.raises(UniFiAuthError) as excinfo:
        adapter.get_clients()
    assert "UNIFI_API_KEY is not configured" in str(excinfo.value)

    success, msg = adapter.test_connection()
    assert success is False
    assert "UNIFI_API_KEY is not configured" in msg


def test_official_adapter_site_resolution(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    monkeypatch.setattr(settings, "UNIFI_SITE", "default")
    mock_http = MagicMock()

    adapter = OfficialUniFiAdapter(shared_http=mock_http)

    # 1. Matching by internalReference
    mock_sites_payload = {
        "data": [
            {
                "id": "site-uuid-1",
                "name": "My Main Site",
                "internalReference": "default",
            }
        ]
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: mock_sites_payload)
    site_id = adapter._resolve_site()
    assert site_id == "site-uuid-1"

    # Reset cache
    adapter._resolved_site_id = None
    adapter._api_base_path = None

    # 2. Matching single site with name 'Default'
    mock_sites_single = {
        "data": [
            {
                "id": "site-uuid-single",
                "name": "Default",
            }
        ]
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: mock_sites_single)
    site_id = adapter._resolve_site()
    assert site_id == "site-uuid-single"

    # Reset cache
    adapter._resolved_site_id = None
    adapter._api_base_path = None

    # 3. Site not found
    mock_sites_unmatched = {
        "data": [
            {
                "id": "site-other",
                "name": "Branch Office",
                "internalReference": "branch",
            },
            {
                "id": "site-warehouse",
                "name": "Warehouse",
                "internalReference": "wh1",
            },
        ]
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: mock_sites_unmatched)
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter._resolve_site()
    assert "not found in official API sites" in str(excinfo.value)


def test_official_adapter_get_clients_pagination(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Simulate 2 pages of results with page size limit=2:
    # Page 1: returns 2 items, totalCount=3
    page1 = {
        "data": [
            {"macAddress": "00:00:00:00:00:01", "name": "Device 1"},
            {"macAddress": "00:00:00:00:00:02", "name": "Device 2"},
        ],
        "totalCount": 3,
        "limit": 2,
        "offset": 0,
    }
    # Page 2: returns 1 item, totalCount=3
    page2 = {
        "data": [
            {"macAddress": "00:00:00:00:00:03", "name": "Device 3"},
        ],
        "totalCount": 3,
        "limit": 2,
        "offset": 2,
    }

    mock_resp1 = MagicMock(status_code=200, json=lambda: page1)
    mock_resp2 = MagicMock(status_code=200, json=lambda: page2)
    mock_http.get.side_effect = [mock_resp1, mock_resp2]

    clients = adapter.get_clients()
    assert len(clients) == 3
    assert [c.mac for c in clients] == [
        "00:00:00:00:00:01",
        "00:00:00:00:00:02",
        "00:00:00:00:00:03",
    ]


def test_official_adapter_get_clients_empty_site(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    mock_http.get.return_value = MagicMock(
        status_code=200, json=lambda: {"data": [], "totalCount": 0}
    )

    clients = adapter.get_clients()
    assert clients == []


def test_official_adapter_error_handling(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # 401 Unauthorized
    mock_http.get.return_value = MagicMock(status_code=401)
    with pytest.raises(UniFiAuthError):
        adapter.get_clients()

    # 403 Forbidden
    mock_http.get.return_value = MagicMock(status_code=403)
    with pytest.raises(UniFiPermissionError):
        adapter.get_clients()

    # 404 Endpoint not found (invalidates cached site ID)
    mock_http.get.return_value = MagicMock(status_code=404)
    with pytest.raises(UniFiResponseError):
        adapter.get_clients()

    # Restore cached site ID for subsequent client endpoint tests
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Connection error
    mock_http.get.side_effect = httpx.ConnectError("Connection refused")
    with pytest.raises(UniFiConnectionError):
        adapter.get_clients()

    # Timeout
    mock_http.get.side_effect = httpx.ReadTimeout("Timeout")
    with pytest.raises(UniFiTimeoutError):
        adapter.get_clients()

    # Malformed JSON
    mock_http.get.side_effect = None
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: "not a dict")
    with pytest.raises(UniFiResponseError):
        adapter.get_clients()

    # Missing "data" field
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {})
    with pytest.raises(UniFiResponseError):
        adapter.get_clients()

    # Non-dict item in "data" array raises UniFiResponseError
    mock_http.get.return_value = MagicMock(
        status_code=200, json=lambda: {"data": ["invalid_string_entry"]}
    )
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "expected dict" in str(excinfo.value)


def test_official_adapter_skips_non_mac_clients(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Mixed inventory: 1 physical LAN client, 1 Teleport/VPN client without MAC, 1 duplicate MAC
    mixed_data = {
        "data": [
            {
                "id": "c-lan-1",
                "macAddress": "00:11:22:33:44:55",
                "ipAddress": "192.168.1.50",
                "name": "LAN-PC",
                "type": "WIRED",
            },
            {
                "id": "c-vpn-1",
                "ipAddress": "10.8.0.2",
                "name": "Remote-User",
                "type": "VPN",
                "macAddress": None,
            },
            {
                "id": "c-lan-dup",
                "macAddress": "00:11:22:33:44:55",
                "ipAddress": "192.168.1.50",
                "name": "Duplicate-Record",
            },
        ],
        "totalCount": 3,
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: mixed_data)
    clients = adapter.get_clients()

    # The VPN client was safely skipped without error, duplicate was deduplicated
    assert len(clients) == 1
    assert clients[0].mac == "00:11:22:33:44:55"
    assert clients[0].name == "LAN-PC"


def test_official_adapter_ambiguous_site_error(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    monkeypatch.setattr(settings, "UNIFI_SITE", "default")
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)

    # Two sites both matching 'default'
    ambiguous_sites = {
        "data": [
            {"id": "site-1", "name": "Default", "internalReference": "default"},
            {"id": "site-2", "name": "Default", "internalReference": "default"},
        ]
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: ambiguous_sites)
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter._resolve_site()
    assert "Ambiguous UniFi site match" in str(excinfo.value)


def test_official_adapter_resolve_site_malformed_records(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)

    # Malformed site list: item is not a dict
    mock_http.get.return_value = MagicMock(
        status_code=200, json=lambda: {"data": ["not-a-dict-site-record"]}
    )
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter._resolve_site()
    assert "Malformed site record" in str(excinfo.value)


def test_official_adapter_stale_site_invalidation_on_404(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "stale-site-id"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Controller returns 404 (stale site ID)
    mock_http.get.return_value = MagicMock(status_code=404)
    with pytest.raises(UniFiResponseError):
        adapter.get_clients()

    # Verify site ID cache was invalidated
    assert adapter._resolved_site_id is None


def test_official_adapter_test_connection(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)

    # Sites response + Clients response
    sites_resp = MagicMock(
        status_code=200,
        json=lambda: {"data": [{"id": "s-1", "name": "Default", "internalReference": "default"}]},
    )
    clients_resp = MagicMock(
        status_code=200,
        json=lambda: {"data": [], "totalCount": 0},
    )
    mock_http.get.side_effect = [sites_resp, clients_resp]

    success, msg = adapter.test_connection()
    assert success is True
    assert "Successfully connected to UniFi official API" in msg


def test_unifi_factory_mode_selection(monkeypatch):
    close_unifi_adapter()

    # 1. Mode legacy
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "legacy")
    adapter = create_unifi_adapter()
    assert isinstance(adapter, LegacyUniFiAdapter)
    assert adapter.adapter_name == "legacy"

    # 2. Mode official
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "official")
    adapter = create_unifi_adapter()
    assert isinstance(adapter, OfficialUniFiAdapter)
    assert adapter.adapter_name == "official"

    # 3. Mode auto with no API key
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "auto")
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr(""))
    adapter = create_unifi_adapter()
    assert isinstance(adapter, LegacyUniFiAdapter)

    # 4. Mode auto with API key in mock mode
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "auto")
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("mock-key"))
    adapter = create_unifi_adapter()
    assert isinstance(adapter, OfficialUniFiAdapter)

    close_unifi_adapter()


def test_factory_auto_mode_auth_error_not_swallowed(monkeypatch):
    close_unifi_adapter()
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "auto")
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("invalid-key"))

    mock_client = MagicMock()
    # 401 Unauthorized during probe
    mock_client.get.return_value = MagicMock(status_code=401)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: mock_client)

    with pytest.raises(UniFiAuthError):
        create_unifi_adapter()

    # Discarded adapter must be closed
    assert mock_client.close.called
    close_unifi_adapter()


def test_factory_auto_mode_permission_error_not_swallowed(monkeypatch):
    close_unifi_adapter()
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "auto")
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("forbidden-key"))

    mock_client = MagicMock()
    # 403 Forbidden during probe
    mock_client.get.return_value = MagicMock(status_code=403)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: mock_client)

    with pytest.raises(UniFiPermissionError):
        create_unifi_adapter()

    assert mock_client.close.called
    close_unifi_adapter()


def test_factory_auto_mode_unsupported_api_falls_back_to_legacy(monkeypatch):
    close_unifi_adapter()
    monkeypatch.setattr(settings, "UNIFI_API_MODE", "auto")
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("some-key"))

    mock_client = MagicMock()
    # 404 Endpoint not found for candidate official paths
    mock_client.get.return_value = MagicMock(status_code=404)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: mock_client)

    adapter = create_unifi_adapter()
    assert isinstance(adapter, LegacyUniFiAdapter)
    assert mock_client.close.called
    close_unifi_adapter()


def test_pagination_with_vpn_clients_and_duplicates(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # 2 pages with 2 raw records each (totalCount=4)
    # Page 1: 1 physical LAN client + 1 VPN client without MAC
    page1 = {
        "data": [
            {"id": "c-1", "macAddress": "00:11:22:33:44:55", "name": "LAN-1"},
            {"id": "c-vpn", "type": "VPN", "name": "Teleport-1", "macAddress": None},
        ],
        "totalCount": 4,
        "limit": 2,
        "offset": 0,
    }
    # Page 2: 1 duplicate MAC of LAN-1 + 1 new physical client
    page2 = {
        "data": [
            {"id": "c-1-dup", "macAddress": "00:11:22:33:44:55", "name": "LAN-1-Dup"},
            {"id": "c-2", "macAddress": "00:11:22:33:44:66", "name": "LAN-2"},
        ],
        "totalCount": 4,
        "limit": 2,
        "offset": 2,
    }

    mock_http.get.side_effect = [
        MagicMock(status_code=200, json=lambda: page1),
        MagicMock(status_code=200, json=lambda: page2),
    ]

    clients = adapter.get_clients()
    # Exactly 2 unique physical clients returned
    assert len(clients) == 2
    assert [c.mac for c in clients] == ["00:11:22:33:44:55", "00:11:22:33:44:66"]
    # Pagination completed cleanly after 2 pages
    assert mock_http.get.call_count == 2


def test_pagination_premature_empty_page_rejected(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Server claims totalCount=10, but returns empty page at offset 0
    empty_premature = {
        "data": [],
        "totalCount": 10,
        "offset": 0,
        "limit": 10,
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: empty_premature)

    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "Premature empty page" in str(excinfo.value)


def test_pagination_inconsistent_offset_rejected(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Server returns wrong offset (99 instead of 0)
    bad_offset = {
        "data": [{"macAddress": "00:11:22:33:44:55"}],
        "totalCount": 1,
        "offset": 99,
        "limit": 10,
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: bad_offset)

    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "Inconsistent pagination offset" in str(excinfo.value)


def test_pagination_truncated_page_rejected(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Server returns 1 item with limit 10, but totalCount is 5 (truncated)
    truncated = {
        "data": [{"macAddress": "00:11:22:33:44:55"}],
        "totalCount": 5,
        "offset": 0,
        "limit": 10,
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: truncated)

    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "Incomplete pagination response" in str(excinfo.value)


def test_pagination_overflow_rejected(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Server claims totalCount=1, but returns 2 items
    overflow = {
        "data": [
            {"macAddress": "00:11:22:33:44:55"},
            {"macAddress": "00:11:22:33:44:66"},
        ],
        "totalCount": 1,
        "offset": 0,
        "limit": 10,
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: overflow)

    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "Pagination overflow" in str(excinfo.value)


def test_pagination_max_pages_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)
    adapter._resolved_site_id = "site-test"
    adapter._api_base_path = "https://unifi.example.local/proxy/network/integration/v1"

    # Infinite loop simulation: server never terminates, returning 1 item per page without totalCount
    def endless_response(*args, **kwargs):
        params = kwargs.get("params", {})
        off = params.get("offset", 0)
        return MagicMock(
            status_code=200,
            json=lambda: {
                "data": [{"macAddress": f"00:00:00:00:00:{off:02x}"}],
                "offset": off,
                "limit": 1,
            },
        )

    mock_http.get.side_effect = endless_response

    adapter.max_pages = 3
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "Maximum pagination pages" in str(excinfo.value)
    assert mock_http.get.call_count == 3


def test_resolve_site_pagination(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    monkeypatch.setattr(settings, "UNIFI_SITE", "target-site")
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)

    # 2 pages of sites:
    page1 = {
        "data": [
            {"id": "site-1", "name": "Branch Office", "internalReference": "branch"},
        ],
        "totalCount": 2,
        "limit": 1,
        "offset": 0,
    }
    page2 = {
        "data": [
            {"id": "site-2", "name": "Target Site", "internalReference": "target-site"},
        ],
        "totalCount": 2,
        "limit": 1,
        "offset": 1,
    }

    mock_http.get.side_effect = [
        MagicMock(status_code=200, json=lambda: page1),
        MagicMock(status_code=200, json=lambda: page2),
    ]

    site_id = adapter._resolve_site()
    assert site_id == "site-2"
    assert mock_http.get.call_count == 2


def test_resolve_site_exact_match_precedence(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "UNIFI_API_KEY", SecretStr("test-api-key"))
    mock_http = MagicMock()
    adapter = OfficialUniFiAdapter(shared_http=mock_http)

    # Multi-site console:
    # Site 1: id="uuid-1", name="default", internalReference="branch"
    # Site 2: id="uuid-2", name="Branch Office", internalReference="default"
    # Site 3: id="default", name="Tertiary", internalReference="tertiary"
    sites_payload = {
        "data": [
            {"id": "uuid-1", "name": "default", "internalReference": "branch"},
            {"id": "uuid-2", "name": "Branch Office", "internalReference": "default"},
            {"id": "default", "name": "Tertiary", "internalReference": "tertiary"},
        ],
        "totalCount": 3,
        "limit": 10,
        "offset": 0,
    }
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: sites_payload)

    # 1. Configured = "default" -> Tier 1 (ID == "default") takes priority!
    monkeypatch.setattr(settings, "UNIFI_SITE", "default")
    adapter._resolved_site_id = None
    adapter._api_base_path = None
    assert adapter._resolve_site() == "default"

    # 2. Configured = "branch" -> Tier 2 (internalReference == "branch") takes priority over Tier 3/4
    monkeypatch.setattr(settings, "UNIFI_SITE", "branch")
    adapter._resolved_site_id = None
    adapter._api_base_path = None
    assert adapter._resolve_site() == "uuid-1"

    # 3. Configured = "Branch Office" -> Tier 3 (exact name) matches uuid-2
    monkeypatch.setattr(settings, "UNIFI_SITE", "Branch Office")
    adapter._resolved_site_id = None
    adapter._api_base_path = None
    assert adapter._resolve_site() == "uuid-2"

    # 4. Configured = "branch office" (lowercase) -> Tier 4 matches uuid-2
    monkeypatch.setattr(settings, "UNIFI_SITE", "branch office")
    adapter._resolved_site_id = None
    adapter._api_base_path = None
    assert adapter._resolve_site() == "uuid-2"
