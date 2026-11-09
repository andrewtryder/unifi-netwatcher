"""Unit and integration tests for SSID, access point roaming, and network change detection."""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.config import settings
from app.db import Base, utcnow
from app.models import (
    ConnectivityState,
    Device,
    DeviceStatus,
    Event,
    NotificationChannel,
)
from app.scanner import run_scan
from app.services.connectivity import ConnectivityService
from app.services.notifications import (
    BehavioralNotificationRules,
    save_behavioral_notification_rules,
)
from app.unifi.base import UniFiAdapter
from app.unifi.factory import close_unifi_adapter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    close_unifi_adapter()
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "DEVICE_OFFLINE_MISSED_SCANS", 2)
    monkeypatch.setattr(settings, "DEVICE_OFFLINE_GRACE_SECONDS", 600)
    monkeypatch.setattr(settings, "NOTIFY_DEVICE_SSID_CHANGED", False)
    monkeypatch.setattr(settings, "NOTIFY_DEVICE_AP_CHANGED", False)
    monkeypatch.setattr(settings, "NOTIFY_DEVICE_NETWORK_CHANGED", False)
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.main.SessionLocal", TestingSessionLocal)

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    close_unifi_adapter()


class MockUniFiAdapter(UniFiAdapter):
    def __init__(self, clients: list[dict]):
        self._clients = clients

    def get_clients(self) -> list[dict]:
        return self._clients

    def block_client(self, mac: str) -> bool:
        return True

    def unblock_client(self, mac: str) -> bool:
        return True

    def test_connection(self) -> tuple[bool, str]:
        return True, "Connected"

    def close(self) -> None:
        pass


# ==============================================================================
# 1. ConnectivityService Unit Tests for Network Transitions
# ==============================================================================


def test_detect_ap_change_when_online():
    service = ConnectivityService()
    now = utcnow()
    device = Device(
        id=1,
        mac="aa:bb:cc:dd:ee:01",
        display_name="Phone",
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Home-Wifi",
        ip="192.168.1.50",
    )

    transitions = service.detect_network_transitions(
        device,
        observed_ip="192.168.1.50",
        observed_ssid="Home-Wifi",
        observed_ap_mac="11:22:33:44:55:02",
        now=now,
    )

    assert len(transitions) == 1
    t = transitions[0]
    assert t.event_type == "device_ap_changed"
    assert t.event_metadata["old_ap_mac"] == "11:22:33:44:55:01"
    assert t.event_metadata["new_ap_mac"] == "11:22:33:44:55:02"
    assert "roamed to AP" in t.event_message


def test_detect_ssid_change_when_online():
    service = ConnectivityService()
    now = utcnow()
    device = Device(
        id=2,
        mac="aa:bb:cc:dd:ee:02",
        display_name="Tablet",
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Home-2.4G",
        ip="192.168.1.51",
    )

    transitions = service.detect_network_transitions(
        device,
        observed_ip="192.168.1.51",
        observed_ssid="Home-5G",
        observed_ap_mac="11:22:33:44:55:01",
        now=now,
    )

    assert len(transitions) == 1
    t = transitions[0]
    assert t.event_type == "device_ssid_changed"
    assert t.event_metadata["old_ssid"] == "Home-2.4G"
    assert t.event_metadata["new_ssid"] == "Home-5G"
    assert "SSID changed" in t.event_message


def test_detect_network_ip_change_when_online():
    service = ConnectivityService()
    now = utcnow()
    device = Device(
        id=3,
        mac="aa:bb:cc:dd:ee:03",
        display_name="Workstation",
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Home-Wifi",
        ip="192.168.1.52",
    )

    transitions = service.detect_network_transitions(
        device,
        observed_ip="192.168.1.152",
        observed_ssid="Home-Wifi",
        observed_ap_mac="11:22:33:44:55:01",
        now=now,
    )

    assert len(transitions) == 1
    t = transitions[0]
    assert t.event_type == "device_network_changed"
    assert t.event_metadata["old_ip"] == "192.168.1.52"
    assert t.event_metadata["new_ip"] == "192.168.1.152"
    assert "IP changed" in t.event_message


def test_no_transitions_on_identical_observations():
    service = ConnectivityService()
    now = utcnow()
    device = Device(
        id=4,
        mac="aa:bb:cc:dd:ee:04",
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Home-Wifi",
        ip="192.168.1.50",
    )

    transitions = service.detect_network_transitions(
        device,
        observed_ip="192.168.1.50",
        observed_ssid="Home-Wifi",
        observed_ap_mac="11:22:33:44:55:01",
        now=now,
    )
    assert len(transitions) == 0


def test_no_transitions_on_missing_or_none_metadata():
    service = ConnectivityService()
    now = utcnow()
    device = Device(
        id=5,
        mac="aa:bb:cc:dd:ee:05",
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Home-Wifi",
        ip="192.168.1.50",
    )

    # UniFi temporarily omits metadata or returns None
    transitions = service.detect_network_transitions(
        device,
        observed_ip=None,
        observed_ssid=None,
        observed_ap_mac=None,
        now=now,
    )
    assert len(transitions) == 0


def test_no_transitions_when_device_is_offline_or_unknown():
    service = ConnectivityService()
    now = utcnow()
    device = Device(
        id=6,
        mac="aa:bb:cc:dd:ee:06",
        connectivity_state=ConnectivityState.OFFLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Home-Wifi",
        ip="192.168.1.50",
    )

    transitions = service.detect_network_transitions(
        device,
        observed_ip="192.168.1.99",
        observed_ssid="New-Wifi",
        observed_ap_mac="11:22:33:44:55:02",
        now=now,
    )
    assert len(transitions) == 0


# ==============================================================================
# 2. Scanner Integration Tests for Roaming
# ==============================================================================


def test_scanner_tracks_roaming_without_disconnecting(monkeypatch):
    """Switching APs must record device_ap_changed while remaining online without false disconnect."""
    db = TestingSessionLocal()
    now = utcnow()

    # Pre-seed device online on AP 01
    device = Device(
        mac="aa:bb:cc:dd:ee:10",
        hostname="mobile-phone",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Main-WLAN",
        ip="192.168.1.80",
        last_connected_at=now - timedelta(hours=1),
        consecutive_missed_scans=0,
    )
    db.add(device)
    db.commit()

    # Scan observes device roaming to AP 02
    clients = [
        {
            "mac": "aa:bb:cc:dd:ee:10",
            "hostname": "mobile-phone",
            "ip": "192.168.1.80",
            "essid": "Main-WLAN",
            "ap_mac": "11:22:33:44:55:02",
        }
    ]
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter(clients))

    result = run_scan(db)
    assert result["success"] is True

    db.refresh(device)
    # Roaming must NOT change connectivity state to offline!
    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.consecutive_missed_scans == 0
    assert device.offline_candidate_since is None
    # Approval status untouched
    assert device.status == DeviceStatus.TRUSTED.value
    # AP updated
    assert device.last_ap_mac == "11:22:33:44:55:02"

    # Verify device_ap_changed event exists
    events = (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "device_ap_changed")
        .all()
    )
    assert len(events) == 1
    assert events[0].metadata_json["old_ap_mac"] == "11:22:33:44:55:01"
    assert events[0].metadata_json["new_ap_mac"] == "11:22:33:44:55:02"

    # Verify NO disconnect or reconnect events were created
    disc = (
        db.query(Event)
        .filter(
            Event.device_id == device.id,
            Event.event_type.in_(["device_disconnected", "device_reconnected"]),
        )
        .all()
    )
    assert len(disc) == 0

    db.close()


def test_scanner_roaming_back_to_original_ap(monkeypatch):
    """Returning to a previously used AP is detected correctly."""
    db = TestingSessionLocal()
    now = utcnow()

    device = Device(
        mac="aa:bb:cc:dd:ee:11",
        hostname="tablet",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:02",
        last_ssid="Main-WLAN",
        ip="192.168.1.81",
        last_connected_at=now - timedelta(hours=1),
    )
    db.add(device)
    db.commit()

    # Roams back to AP 01
    clients = [
        {
            "mac": "aa:bb:cc:dd:ee:11",
            "hostname": "tablet",
            "ip": "192.168.1.81",
            "essid": "Main-WLAN",
            "ap_mac": "11:22:33:44:55:01",
        }
    ]
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter(clients))

    result = run_scan(db)
    assert result["success"] is True

    db.refresh(device)
    assert device.last_ap_mac == "11:22:33:44:55:01"

    event = (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "device_ap_changed")
        .first()
    )
    assert event is not None
    assert event.metadata_json["old_ap_mac"] == "11:22:33:44:55:02"
    assert event.metadata_json["new_ap_mac"] == "11:22:33:44:55:01"

    db.close()


def test_transiently_missing_ap_does_not_flap(monkeypatch):
    """If UniFi transiently returns None/empty AP MAC, it must not cause false roaming."""
    db = TestingSessionLocal()

    device = Device(
        mac="aa:bb:cc:dd:ee:12",
        hostname="laptop",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Main-WLAN",
        ip="192.168.1.82",
    )
    db.add(device)
    db.commit()

    # Scan 1: UniFi omits ap_mac
    clients_no_ap = [
        {
            "mac": "aa:bb:cc:dd:ee:12",
            "hostname": "laptop",
            "ip": "192.168.1.82",
            "essid": "Main-WLAN",
            "ap_mac": None,
        }
    ]
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter(clients_no_ap))

    run_scan(db)
    db.refresh(device)
    # Retains previous AP MAC
    assert device.last_ap_mac == "11:22:33:44:55:01"
    assert (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "device_ap_changed")
        .count()
        == 0
    )

    # Scan 2: UniFi provides AP 01 again
    clients_with_ap = [
        {
            "mac": "aa:bb:cc:dd:ee:12",
            "hostname": "laptop",
            "ip": "192.168.1.82",
            "essid": "Main-WLAN",
            "ap_mac": "11:22:33:44:55:01",
        }
    ]
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter(clients_with_ap))

    run_scan(db)
    # Still no false roaming event!
    assert (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "device_ap_changed")
        .count()
        == 0
    )

    db.close()


def test_scanner_detects_ssid_and_ip_transitions(monkeypatch):
    """Scanner detects SSID and IP transitions when device is online."""
    db = TestingSessionLocal()

    device = Device(
        mac="aa:bb:cc:dd:ee:13",
        hostname="smart-tv",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="IoT-Network",
        ip="192.168.1.200",
    )
    db.add(device)
    db.commit()

    clients = [
        {
            "mac": "aa:bb:cc:dd:ee:13",
            "hostname": "smart-tv",
            "ip": "192.168.1.250",
            "essid": "Guest-Network",
            "ap_mac": "11:22:33:44:55:01",
        }
    ]
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter(clients))

    run_scan(db)
    db.refresh(device)

    assert device.ip == "192.168.1.250"
    assert device.last_ssid == "Guest-Network"

    ssid_event = (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "device_ssid_changed")
        .first()
    )
    assert ssid_event is not None
    assert ssid_event.metadata_json["old_ssid"] == "IoT-Network"
    assert ssid_event.metadata_json["new_ssid"] == "Guest-Network"

    ip_event = (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "device_network_changed")
        .first()
    )
    assert ip_event is not None
    assert ip_event.metadata_json["old_ip"] == "192.168.1.200"
    assert ip_event.metadata_json["new_ip"] == "192.168.1.250"

    db.close()


def test_roaming_notifications_dispatched_when_rule_enabled(monkeypatch):
    """When notify_device_ap_changed is enabled, roaming dispatches an alert."""
    db = TestingSessionLocal()

    rules = BehavioralNotificationRules(notify_device_ap_changed=True, cooldown_seconds=3600)
    save_behavioral_notification_rules(db, rules)

    device = Device(
        mac="aa:bb:cc:dd:ee:14",
        hostname="warehouse-scanner",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_ap_mac="11:22:33:44:55:01",
        last_ssid="Warehouse-WLAN",
        ip="10.0.0.50",
    )
    db.add(device)

    sent_alerts: list[str] = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            sent_alerts.append(msg)
            return True, 200, "ok", ""

    db.add(
        NotificationChannel(
            name="channel",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/test"},
        )
    )
    db.commit()

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})

    clients = [
        {
            "mac": "aa:bb:cc:dd:ee:14",
            "hostname": "warehouse-scanner",
            "ip": "10.0.0.50",
            "essid": "Warehouse-WLAN",
            "ap_mac": "11:22:33:44:55:02",
        }
    ]
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter(clients))

    run_scan(db)

    assert len(sent_alerts) == 1
    assert "Device Access Point Roamed" in sent_alerts[0]
    assert "11:22:33:44:55:01 -> 11:22:33:44:55:02" in sent_alerts[0]

    db.close()
