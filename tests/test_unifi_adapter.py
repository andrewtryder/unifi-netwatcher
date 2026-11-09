from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from app.config import settings
from app.unifi import (
    LegacyUniFiAdapter,
    UniFiAdapter,
    UniFiAuthError,
    UniFiCapabilities,
    UnifiClient,
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


def test_unifi_client_device_attributes_and_dict_access():
    device = UniFiClientDevice(
        mac="aa:bb:cc:dd:ee:ff",
        ip="192.168.1.10",
        hostname="my-laptop",
        rssi=-55,
        tx_rate_bps=300000,
        raw_data={"custom_field": "custom_val"},
    )
    assert device.mac == "aa:bb:cc:dd:ee:ff"
    assert device.ip == "192.168.1.10"
    assert device.hostname == "my-laptop"
    assert device.rssi == -55

    # Dict-like compatibility
    assert device["mac"] == "aa:bb:cc:dd:ee:ff"
    assert device.get("ip") == "192.168.1.10"
    assert device.get("signal") == -55
    assert device.get("tx_rate") == 300000
    assert device.get("custom_field") == "custom_val"
    assert device.get("nonexistent", "fallback") == "fallback"
    assert "mac" in device
    assert "custom_field" in device


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


def test_unifi_capabilities():
    caps = UniFiCapabilities()
    assert caps.can_block is True
    assert caps.can_unblock is True
    assert caps.has_wifi_telemetry is True
    assert caps.has_switch_port is True
    assert caps.supports_pagination is False


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

    # 401 Invalid credentials
    mock_http.post.return_value = MagicMock(status_code=401)
    assert adapter.login() is False
    assert adapter._logged_in is False

    # 403 Forbidden
    mock_http.post.return_value = MagicMock(status_code=403)
    assert adapter.login() is False

    # Connection error
    mock_http.post.side_effect = httpx.ConnectError("Network is unreachable")
    assert adapter.login() is False


def test_legacy_adapter_get_clients_empty_site(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()

    adapter = LegacyUniFiAdapter(shared_http=mock_http)
    adapter._logged_in = True

    # Controller returns successful response with zero clients
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {"data": []})

    clients = adapter.get_clients()
    assert clients == []


def test_legacy_adapter_get_clients_errors(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()
    adapter = LegacyUniFiAdapter(shared_http=mock_http)
    adapter._logged_in = True

    # 401 on GET, re-login also fails
    mock_http.get.return_value = MagicMock(status_code=401)
    mock_http.post.return_value = MagicMock(status_code=401)
    with pytest.raises(UniFiAuthError):
        adapter.get_clients()

    # Reset login state for subsequent tests
    adapter._logged_in = True
    mock_http.post.return_value = MagicMock(status_code=200)

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

    # Malformed response
    mock_http.get.side_effect = None
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: "not a dict")
    with pytest.raises(UniFiResponseError):
        adapter.get_clients()

    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {"data": "not a list"})
    with pytest.raises(UniFiResponseError):
        adapter.get_clients()


def test_legacy_adapter_test_connection(monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    mock_http = MagicMock()
    adapter = LegacyUniFiAdapter(shared_http=mock_http)

    # Login failure
    mock_http.post.return_value = MagicMock(status_code=401)
    success, msg = adapter.test_connection()
    assert success is False
    assert "Authentication failed" in msg

    # Login ok and get_clients ok
    mock_http.post.return_value = MagicMock(status_code=200)
    mock_http.get.return_value = MagicMock(status_code=200, json=lambda: {"data": []})
    success, msg = adapter.test_connection()
    assert success is True
    assert "Successfully connected" in msg


def test_backward_compatible_unifi_client():
    client = get_unifi_client()
    assert isinstance(client, UnifiClient)
    assert isinstance(client, LegacyUniFiAdapter)
    assert isinstance(client, UniFiAdapter)

    # Adapter factory
    adapter = get_unifi_adapter()
    assert isinstance(adapter, LegacyUniFiAdapter)

    close_unifi_client()
    close_unifi_adapter()
