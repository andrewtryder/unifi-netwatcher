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
    assert adapter.capabilities.has_wifi_telemetry is True
    assert adapter.capabilities.has_switch_port is True

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

    # Simulate 2 pages of results:
    # Page 1: returns 2 items, totalCount=3
    page1 = {
        "data": [
            {"macAddress": "00:00:00:00:00:01", "name": "Device 1"},
            {"macAddress": "00:00:00:00:00:02", "name": "Device 2"},
        ],
        "totalCount": 3,
    }
    # Page 2: returns 1 item, totalCount=3
    page2 = {
        "data": [
            {"macAddress": "00:00:00:00:00:03", "name": "Device 3"},
        ],
        "totalCount": 3,
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
