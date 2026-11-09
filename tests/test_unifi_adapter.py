from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import MagicMock

import httpx
import pytest
from app.config import settings
from app.unifi import (
    LegacyUniFiAdapter,
    UniFiAdapter,
    UniFiAuthError,
    UniFiCapabilities,
    UniFiClientDevice,
    UniFiConnectionError,
    UniFiPermissionError,
    UniFiResponseError,
    UniFiTimeoutError,
    close_unifi_adapter,
    close_unifi_client,
    get_unifi_adapter,
    get_unifi_client,
)
from app.unifi.models import as_bool, as_int


def test_as_int_and_as_bool_helpers():
    assert as_int("42") == 42
    assert as_int(42) == 42
    assert as_int(None) is None
    assert as_int("invalid") is None

    assert as_bool(True) is True
    assert as_bool(False) is False
    assert as_bool(1) is True
    assert as_bool(0) is False
    assert as_bool("true") is True
    assert as_bool("1") is True
    assert as_bool("yes") is True
    assert as_bool("false") is False
    assert as_bool("0") is False
    assert as_bool("no") is False
    assert as_bool(None) is None
    assert as_bool("unknown") is None


def test_unifi_client_device_attributes_and_mapping_protocol():
    device = UniFiClientDevice(
        mac="aa:bb:cc:dd:ee:ff",
        ip="192.168.1.10",
        hostname="my-laptop",
        rssi=-55,
        tx_rate_bps=300000,
        raw_data={"custom_field": "custom_val"},
    )
    # Attribute access
    assert device.mac == "aa:bb:cc:dd:ee:ff"
    assert device.ip == "192.168.1.10"
    assert device.hostname == "my-laptop"
    assert device.rssi == -55

    # Conforms to collections.abc.Mapping
    assert isinstance(device, Mapping)
    assert len(device) > 0
    assert "mac" in list(device)
    assert "custom_field" in list(device)

    # Dictionary conversion
    as_dict = dict(device)
    assert isinstance(as_dict, dict)
    assert as_dict["mac"] == "aa:bb:cc:dd:ee:ff"
    assert as_dict["custom_field"] == "custom_val"

    # Subscripting and .get()
    assert device["mac"] == "aa:bb:cc:dd:ee:ff"
    assert device.get("ip") == "192.168.1.10"
    assert device.get("signal") == -55
    assert device.get("tx_rate") == 300000
    assert device.get("custom_field") == "custom_val"
    assert device.get("nonexistent", "fallback") == "fallback"
    assert "mac" in device
    assert "custom_field" in device
    assert "signal" in device

    with pytest.raises(KeyError):
        _ = device["nonexistent_key"]


def test_unifi_client_device_from_legacy_dict():
    raw = {
        "mac": "11:22:33:44:55:66",
        "ip": "10.0.0.5",
        "hostname": "phone",
        "is_wired": "false",
        "signal": -68,
        "tx_rate": 150000,
        "rx_rate": 120000,
        "tx_bytes-r": 2048,
        "rx_bytes-r": 4096,
        "channel": "36",
        "sw_port": "4",
        "oui": "Apple",
        "essid": "HomeWiFi",
        "ap_mac": "aa:11:22:33:44:55",
    }
    dev = UniFiClientDevice.from_legacy_dict(raw)
    assert dev.mac == "11:22:33:44:55:66"
    assert dev.ip == "10.0.0.5"
    assert dev.hostname == "phone"
    assert dev.is_wired is False
    assert dev.rssi == -68
    assert dev.tx_rate_bps == 150000
    assert dev.rx_rate_bps == 120000
    assert dev.tx_bytes_r == 2048
    assert dev.rx_bytes_r == 4096
    assert dev.channel == 36
    assert dev.sw_port == 4
    assert dev.oui == "Apple"
    assert dev.essid == "HomeWiFi"
    assert dev.raw_data == raw


def test_unifi_capabilities_defaults_and_explicit_legacy():
    # Base defaults should be safely False
    caps = UniFiCapabilities()
    assert caps.can_block is False
    assert caps.can_unblock is False
    assert caps.has_wifi_telemetry is False
    assert caps.has_switch_port is False
    assert caps.supports_pagination is False

    # Base UniFiAdapter has safe defaults
    assert UniFiAdapter.capabilities.can_block is False

    # Legacy adapter explicitly declares its supported capabilities
    legacy_caps = LegacyUniFiAdapter.capabilities
    assert legacy_caps.can_block is True
    assert legacy_caps.can_unblock is True
    assert legacy_caps.has_wifi_telemetry is True
    assert legacy_caps.has_switch_port is True
    assert legacy_caps.supports_pagination is False


def test_legacy_adapter_mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)
    adapter = LegacyUniFiAdapter()
    assert adapter.adapter_name == "legacy"
    assert isinstance(adapter, UniFiAdapter)

    clients = adapter.get_clients()
    assert len(clients) == 4
    assert all(isinstance(c, UniFiClientDevice) for c in clients)

    success, msg = adapter.test_connection()
    assert success is True
    assert "Mock mode" in msg

    assert adapter.block_client("aa:bb:cc:dd:ee:ff") is True
    assert adapter.unblock_client("aa:bb:cc:dd:ee:ff") is True


def test_legacy_adapter_login_flow(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()

    adapter = LegacyUniFiAdapter(shared_http=mock_http)

    # Successful login
    mock_response = MagicMock(status_code=200)
    mock_http.post.return_value = mock_response
    assert adapter.login() is True
    assert adapter._logged_in is True

    # 401 Invalid credentials (preserves boolean return)
    mock_http.post.return_value = MagicMock(status_code=401)
    assert adapter.login() is False
    assert adapter._logged_in is False

    # 403 Forbidden
    mock_http.post.return_value = MagicMock(status_code=403)
    assert adapter.login() is False

    # Connection error
    mock_http.post.side_effect = httpx.ConnectError("Network is unreachable")
    assert adapter.login() is False


def test_legacy_adapter_authenticate_typed_errors(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()
    adapter = LegacyUniFiAdapter(shared_http=mock_http)

    # 401 invalid credentials
    mock_http.post.side_effect = None
    mock_http.post.return_value = MagicMock(status_code=401)
    with pytest.raises(UniFiAuthError):
        adapter._authenticate()

    # 403 permission denied
    mock_http.post.return_value = MagicMock(status_code=403)
    with pytest.raises(UniFiPermissionError):
        adapter._authenticate()

    # Connection failure
    mock_http.post.side_effect = httpx.ConnectError("Connection refused")
    with pytest.raises(UniFiConnectionError):
        adapter._authenticate()

    # Timeout
    mock_http.post.side_effect = httpx.ConnectTimeout("Connect timed out")
    with pytest.raises(UniFiTimeoutError):
        adapter._authenticate()


def test_legacy_adapter_get_clients_initial_login_errors(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()
    adapter = LegacyUniFiAdapter(shared_http=mock_http)
    adapter._logged_in = False

    # Connection error on login raises UniFiConnectionError, NOT UniFiAuthError
    mock_http.post.side_effect = httpx.ConnectError("Controller unreachable")
    with pytest.raises(UniFiConnectionError) as excinfo:
        adapter.get_clients()
    assert "Cannot reach UniFi controller" in str(excinfo.value)

    # Timeout on login raises UniFiTimeoutError, NOT UniFiAuthError
    mock_http.post.side_effect = httpx.ConnectTimeout("Login timed out")
    with pytest.raises(UniFiTimeoutError) as excinfo:
        adapter.get_clients()
    assert "timed out" in str(excinfo.value)

    # 403 on login raises UniFiPermissionError
    mock_http.post.side_effect = None
    mock_http.post.return_value = MagicMock(status_code=403)
    with pytest.raises(UniFiPermissionError):
        adapter.get_clients()

    # 401 on login raises UniFiAuthError
    mock_http.post.return_value = MagicMock(status_code=401)
    with pytest.raises(UniFiAuthError):
        adapter.get_clients()


def test_legacy_adapter_get_clients_reauth_errors(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()
    adapter = LegacyUniFiAdapter(shared_http=mock_http)
    adapter._logged_in = True

    # 401 on GET, re-authentication gets connection error
    mock_http.get.return_value = MagicMock(status_code=401)
    mock_http.post.side_effect = httpx.ConnectError("Controller dropped")
    with pytest.raises(UniFiConnectionError):
        adapter.get_clients()

    # 401 on GET, re-authentication times out
    mock_http.post.side_effect = httpx.ReadTimeout("Timeout on re-login")
    with pytest.raises(UniFiTimeoutError):
        adapter.get_clients()

    # 401 on GET, re-authentication gets 403
    mock_http.post.side_effect = None
    mock_http.post.return_value = MagicMock(status_code=403)
    with pytest.raises(UniFiPermissionError):
        adapter.get_clients()

    # 401 on GET, re-authentication gets 401
    mock_http.post.return_value = MagicMock(status_code=401)
    with pytest.raises(UniFiAuthError):
        adapter.get_clients()


def test_legacy_adapter_get_clients_empty_site(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()

    adapter = LegacyUniFiAdapter(shared_http=mock_http)
    adapter._logged_in = True

    # Controller returns successful response with zero clients
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {"data": []})

    clients = adapter.get_clients()
    assert clients == []


def test_legacy_adapter_get_clients_response_validation(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()
    adapter = LegacyUniFiAdapter(shared_http=mock_http)
    adapter._logged_in = True

    # Controller returns error rc in meta
    mock_http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"meta": {"rc": "error", "msg": "api.err.InvalidSite"}, "data": []},
    )
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "api.err.InvalidSite" in str(excinfo.value)

    # Missing "data" field (e.g. empty JSON dict {})
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {})
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "missing required 'data' field" in str(excinfo.value)

    # "data" is not a list
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {"data": "not a list"})
    with pytest.raises(UniFiResponseError):
        adapter.get_clients()

    # Malformed element in "data" (not a dict)
    mock_http.get.return_value = MagicMock(
        status_code=200, json=lambda: {"data": ["invalid_string"]}
    )
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "expected dict" in str(excinfo.value)

    # Malformed element in "data" (missing MAC)
    mock_http.get.return_value = MagicMock(
        status_code=200, json=lambda: {"data": [{"hostname": "no_mac_device"}]}
    )
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "missing or invalid 'mac'" in str(excinfo.value)

    # Malformed element with empty string MAC
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {"data": [{"mac": "   "}]})
    with pytest.raises(UniFiResponseError) as excinfo:
        adapter.get_clients()
    assert "missing or invalid 'mac'" in str(excinfo.value)

    # Valid device in "data"
    mock_http.get.return_value = MagicMock(
        status_code=200, json=lambda: {"data": [{"mac": "aa:bb:cc:11:22:33", "ip": "10.0.0.1"}]}
    )
    clients = adapter.get_clients()
    assert len(clients) == 1
    assert clients[0].mac == "aa:bb:cc:11:22:33"


def test_legacy_adapter_get_clients_http_errors(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()
    adapter = LegacyUniFiAdapter(shared_http=mock_http)
    adapter._logged_in = True

    # 403 on GET
    mock_http.get.return_value = MagicMock(status_code=403)
    with pytest.raises(UniFiPermissionError):
        adapter.get_clients()

    # Network connection error
    mock_http.get.side_effect = httpx.ConnectError("Connection refused")
    with pytest.raises(UniFiConnectionError):
        adapter.get_clients()

    # Timeout
    mock_http.get.side_effect = httpx.ReadTimeout("Timeout waiting for response")
    with pytest.raises(UniFiTimeoutError):
        adapter.get_clients()


def test_legacy_adapter_test_connection(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()
    adapter = LegacyUniFiAdapter(shared_http=mock_http)

    # Login failure: connection error propagates correctly
    mock_http.post.side_effect = httpx.ConnectError("Network is down")
    success, msg = adapter.test_connection()
    assert success is False
    assert "Cannot reach UniFi controller" in msg

    # Login failure: 401 invalid credentials
    mock_http.post.side_effect = None
    mock_http.post.return_value = MagicMock(status_code=401)
    success, msg = adapter.test_connection()
    assert success is False
    assert "Invalid credentials" in msg

    # Login ok and get_clients ok
    mock_http.post.return_value = MagicMock(status_code=200)
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {"data": []})
    success, msg = adapter.test_connection()
    assert success is True
    assert "Successfully connected" in msg


def test_backward_compatible_unifi_client_and_adapter_singleton():
    client = get_unifi_client()
    adapter = get_unifi_adapter()

    # Unified singleton instance
    assert client is adapter
    assert isinstance(client, LegacyUniFiAdapter)
    assert isinstance(client, UniFiAdapter)

    close_unifi_client()
    close_unifi_adapter()
