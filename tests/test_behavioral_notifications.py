"""Unit and integration tests for configurable behavioral notification rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.db import Base, get_db, utcnow
from app.main import app
from app.models import (
    ConnectivityState,
    Device,
    DeviceStatus,
    Event,
    NotificationChannel,
    NotificationDelivery,
)
from app.scanner import AlertIntent, _deliver_queued_alerts, run_scan
from app.security.service import invalidate_security_cache
from app.services.notifications import (
    BehavioralNotificationRules,
    batch_devices_in_cooldown,
    format_behavioral_alert_message,
    format_duration,
    get_behavioral_notification_rules,
    is_in_behavioral_cooldown,
    save_behavioral_notification_rules,
)
from app.unifi.base import UniFiAdapter
from app.unifi.factory import close_unifi_adapter
from app.unifi.models import UniFiConnectionError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.conftest import basic_auth_header, bootstrap_security_for_tests

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
AUTH = basic_auth_header()


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    close_unifi_adapter()
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "DEVICE_OFFLINE_MISSED_SCANS", 2)
    monkeypatch.setattr(settings, "DEVICE_OFFLINE_GRACE_SECONDS", 600)
    monkeypatch.setattr(settings, "BEHAVIORAL_ALERT_COOLDOWN_SECONDS", 3600)
    monkeypatch.setattr(settings, "NOTIFY_DEVICE_DISCONNECTED", False)
    monkeypatch.setattr(settings, "NOTIFY_DEVICE_RECONNECTED", False)
    monkeypatch.setattr(settings, "NOTIFY_DEVICE_SSID_CHANGED", False)
    monkeypatch.setattr(settings, "NOTIFY_DEVICE_AP_CHANGED", False)
    monkeypatch.setattr(settings, "NOTIFY_DEVICE_NETWORK_CHANGED", False)
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.main.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.security.middleware.SessionLocal", TestingSessionLocal)
    invalidate_security_cache()
    app.dependency_overrides[get_db] = override_get_db

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    bootstrap_security_for_tests(db)
    db.close()
    yield
    close_unifi_adapter()


# ==============================================================================
# 1. Rules & Configuration Unit Tests
# ==============================================================================


def test_default_rules_all_disabled():
    db = TestingSessionLocal()
    rules = get_behavioral_notification_rules(db)
    db.close()

    assert rules.notify_device_disconnected is False
    assert rules.notify_device_reconnected is False
    assert rules.notify_device_ssid_changed is False
    assert rules.notify_device_ap_changed is False
    assert rules.notify_device_network_changed is False
    assert rules.cooldown_seconds == 3600

    assert rules.is_enabled("device_disconnected") is False
    assert rules.is_enabled("device_reconnected") is False
    assert rules.is_enabled("device_ssid_changed") is False
    assert rules.is_enabled("device_ap_changed") is False
    assert rules.is_enabled("device_network_changed") is False
    assert rules.is_enabled("unknown_event") is False


def test_save_and_load_rules_persistence():
    db = TestingSessionLocal()
    new_rules = BehavioralNotificationRules(
        notify_device_disconnected=True,
        notify_device_reconnected=True,
        notify_device_ssid_changed=False,
        notify_device_ap_changed=True,
        notify_device_network_changed=False,
        cooldown_seconds=1800,
    )
    save_behavioral_notification_rules(db, new_rules)
    db.commit()

    loaded = get_behavioral_notification_rules(db)
    db.close()

    assert loaded.notify_device_disconnected is True
    assert loaded.notify_device_reconnected is True
    assert loaded.notify_device_ssid_changed is False
    assert loaded.notify_device_ap_changed is True
    assert loaded.notify_device_network_changed is False
    assert loaded.cooldown_seconds == 1800

    assert loaded.is_enabled("device_disconnected") is True
    assert loaded.is_enabled("device_reconnected") is True
    assert loaded.is_enabled("device_ssid_changed") is False
    assert loaded.is_enabled("device_ap_changed") is True


# ==============================================================================
# 2. Duration & Message Formatting Tests
# ==============================================================================


def test_format_duration():
    assert format_duration(None) == "N/A"
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"
    assert format_duration(60) == "1m"
    assert format_duration(125) == "2m 5s"
    assert format_duration(3600) == "1h"
    assert format_duration(3665) == "1h 1m"
    assert format_duration(86400) == "1d"
    assert format_duration(90000) == "1d 1h"


def test_format_behavioral_alert_messages():
    device = Device(
        mac="aa:bb:cc:dd:ee:ff",
        hostname="livingroom-tv",
        ip="192.168.1.55",
        display_name="Smart TV",
    )
    now = datetime(2026, 11, 9, 14, 0, 0, tzinfo=UTC)

    # Disconnected
    disc_msg = format_behavioral_alert_message(
        "device_disconnected",
        device,
        {
            "online_duration_seconds": 7300,
            "last_seen_at": "2026-11-09T13:45:00Z",
        },
        now=now,
    )
    assert "Device Disconnected" in disc_msg
    assert "Smart TV" in disc_msg
    assert "aa:bb:cc:dd:ee:ff" in disc_msg
    assert "192.168.1.55" in disc_msg
    assert "State: online -> offline" in disc_msg
    assert "Online Duration: 2h 1m" in disc_msg
    assert "2026-11-09T13:45:00Z" in disc_msg

    # Reconnected
    rec_msg = format_behavioral_alert_message(
        "device_reconnected",
        device,
        {
            "offline_duration_seconds": 360,
        },
        now=now,
    )
    assert "Device Reconnected" in rec_msg
    assert "Smart TV" in rec_msg
    assert "State: offline -> online" in rec_msg
    assert "Offline Duration: 6m" in rec_msg

    # SSID changed
    ssid_msg = format_behavioral_alert_message(
        "device_ssid_changed",
        device,
        {"old_ssid": "Home-IoT", "new_ssid": "Home-Fast"},
        now=now,
    )
    assert "Device Wi-Fi Network Changed" in ssid_msg
    assert "SSID: Home-IoT -> Home-Fast" in ssid_msg

    # AP changed
    ap_msg = format_behavioral_alert_message(
        "device_ap_changed",
        device,
        {"old_ap_mac": "11:22:33:44:55:66", "new_ap_mac": "66:55:44:33:22:11"},
        now=now,
    )
    assert "Device Access Point Roamed" in ap_msg
    assert "AP: 11:22:33:44:55:66 -> 66:55:44:33:22:11" in ap_msg

    # Network changed
    net_msg = format_behavioral_alert_message(
        "device_network_changed",
        device,
        {"old_ip": "192.168.1.55", "new_ip": "192.168.1.99"},
        now=now,
    )
    assert "Device IP Address Changed" in net_msg
    assert "IP: 192.168.1.55 -> 192.168.1.99" in net_msg


# ==============================================================================
# 3. Independent Cooldown Tracking Tests
# ==============================================================================


def test_independent_cooldown_disconnect_and_reconnect():
    db = TestingSessionLocal()
    now = utcnow()

    # Device 1 receives a device_disconnected alert
    db.add(
        Event(
            device_id=1,
            event_type="alert_sent",
            message="Sent 1 alerts",
            metadata_json={"event_type": "device_disconnected", "sent_count": 1},
            created_at=now,
        )
    )
    db.commit()

    # Check cooldowns
    assert is_in_behavioral_cooldown(db, 1, "device_disconnected", 3600, now) is True
    # Reconnection must NOT be in cooldown!
    assert is_in_behavioral_cooldown(db, 1, "device_reconnected", 3600, now) is False
    # Discovered must NOT be in cooldown!
    assert is_in_behavioral_cooldown(db, 1, "discovered", 3600, now) is False

    # Check device 2
    assert is_in_behavioral_cooldown(db, 2, "device_disconnected", 3600, now) is False

    # After cooldown period expires
    future = now + timedelta(seconds=3601)
    assert is_in_behavioral_cooldown(db, 1, "device_disconnected", 3600, future) is False

    db.close()


def test_batch_cooldown_check():
    db = TestingSessionLocal()
    now = utcnow()

    db.add(
        Event(
            device_id=10,
            event_type="alert_sent",
            message="Sent 1 alerts",
            metadata_json={"event_type": "device_disconnected"},
            created_at=now,
        )
    )
    db.add(
        Event(
            device_id=20,
            event_type="alert_sent",
            message="Sent 1 alerts",
            metadata_json={"event_type": "device_reconnected"},
            created_at=now,
        )
    )
    db.commit()

    pairs = [
        (10, "device_disconnected"),
        (10, "device_reconnected"),
        (20, "device_disconnected"),
        (20, "device_reconnected"),
        (30, "device_disconnected"),
    ]
    active = batch_devices_in_cooldown(db, pairs, 3600, now)

    assert (10, "device_disconnected") in active
    assert (10, "device_reconnected") not in active
    assert (20, "device_disconnected") not in active
    assert (20, "device_reconnected") in active
    assert (30, "device_disconnected") not in active

    db.close()


# ==============================================================================
# 4. Scanner Integration & Opt-In Safeguards
# ==============================================================================


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


def test_behavioral_notifications_disabled_by_default(monkeypatch):
    """When behavioral alerts are disabled (default), transitions occur without sending alerts."""
    db = TestingSessionLocal()
    now = utcnow()

    # Pre-seed an online device
    device = Device(
        mac="aa:bb:cc:dd:ee:01",
        hostname="test-device",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_connected_at=now - timedelta(hours=1),
        consecutive_missed_scans=1,
        offline_candidate_since=now - timedelta(seconds=700),
    )
    db.add(device)

    # Setup alert channel
    sent_alerts: list[str] = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            sent_alerts.append(msg)
            return True, 200, "ok", ""

    db.add(
        NotificationChannel(
            name="test-channel",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/test"},
        )
    )
    db.commit()

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter([]))

    # Run scan where device is absent and exceeds grace period -> transitions to offline
    result = run_scan(db)
    assert result["success"] is True

    # Transition occurred in DB
    db.refresh(device)
    assert device.connectivity_state == ConnectivityState.OFFLINE.value

    # Event was recorded
    disc_events = (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "device_disconnected")
        .all()
    )
    assert len(disc_events) == 1

    # BUT no notifications were sent because behavioral alerts are disabled by default!
    assert len(sent_alerts) == 0
    queued = db.query(Event).filter(Event.event_type == "alert_queued").all()
    assert len(queued) == 0

    db.close()


def test_behavioral_notifications_disconnect_when_enabled(monkeypatch):
    """When notify_device_disconnected is enabled, disconnecting triggers notification."""
    db = TestingSessionLocal()
    now = utcnow()

    # Enable disconnect alerts in DB
    rules = BehavioralNotificationRules(notify_device_disconnected=True, cooldown_seconds=3600)
    save_behavioral_notification_rules(db, rules)

    device = Device(
        mac="aa:bb:cc:dd:ee:02",
        hostname="camera-front",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_connected_at=now - timedelta(hours=2),
        consecutive_missed_scans=1,
        offline_candidate_since=now - timedelta(seconds=700),
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
            name="test-channel",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/test"},
        )
    )
    db.commit()

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter([]))

    result = run_scan(db)
    assert result["success"] is True

    db.refresh(device)
    assert device.connectivity_state == ConnectivityState.OFFLINE.value

    # Alert WAS sent!
    assert len(sent_alerts) == 1
    assert "Device Disconnected" in sent_alerts[0]
    assert "camera-front" in sent_alerts[0]

    # Delivery recorded
    delivery = db.query(NotificationDelivery).first()
    assert delivery is not None
    assert delivery.success is True

    # Alert sent event recorded
    alert_event = (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "alert_sent")
        .first()
    )
    assert alert_event is not None
    assert alert_event.metadata_json.get("event_type") == "device_disconnected"

    db.close()


def test_behavioral_notifications_reconnect_when_enabled(monkeypatch):
    """When notify_device_reconnected is enabled, reconnecting triggers notification."""
    db = TestingSessionLocal()
    now = utcnow()

    # Enable reconnect alerts in DB
    rules = BehavioralNotificationRules(notify_device_reconnected=True, cooldown_seconds=3600)
    save_behavioral_notification_rules(db, rules)

    device = Device(
        mac="aa:bb:cc:dd:ee:03",
        hostname="laptop-work",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.OFFLINE.value,
        last_connected_at=now - timedelta(days=1),
        last_disconnected_at=now - timedelta(hours=3),
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
            name="test-channel",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/test"},
        )
    )
    db.commit()

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})
    monkeypatch.setattr(
        "app.scanner.get_unifi_adapter",
        lambda: MockUniFiAdapter(
            [{"mac": "aa:bb:cc:dd:ee:03", "hostname": "laptop-work", "ip": "192.168.1.100"}]
        ),
    )

    result = run_scan(db)
    assert result["success"] is True

    db.refresh(device)
    assert device.connectivity_state == ConnectivityState.ONLINE.value

    assert len(sent_alerts) == 1
    assert "Device Reconnected" in sent_alerts[0]
    assert "laptop-work" in sent_alerts[0]
    assert "Offline Duration: 3h" in sent_alerts[0]

    db.close()


def test_cooldown_suppresses_rapid_disconnect_flapping(monkeypatch):
    """A second disconnection alert within cooldown window must be suppressed."""
    db = TestingSessionLocal()
    now = utcnow()

    rules = BehavioralNotificationRules(notify_device_disconnected=True, cooldown_seconds=3600)
    save_behavioral_notification_rules(db, rules)

    device = Device(
        mac="aa:bb:cc:dd:ee:04",
        hostname="flapping-device",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        consecutive_missed_scans=1,
        offline_candidate_since=now - timedelta(seconds=700),
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
            name="test-channel",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/test"},
        )
    )
    db.commit()

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter([]))

    # First disconnect -> alert sent
    run_scan(db)
    assert len(sent_alerts) == 1

    # Device reconnects (reconnect alerts are disabled)
    monkeypatch.setattr(
        "app.scanner.get_unifi_adapter",
        lambda: MockUniFiAdapter([{"mac": "aa:bb:cc:dd:ee:04", "hostname": "flapping-device"}]),
    )
    run_scan(db)

    # Device immediately disconnects again within cooldown
    device.consecutive_missed_scans = 1
    device.offline_candidate_since = utcnow() - timedelta(seconds=700)
    db.commit()

    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter([]))
    run_scan(db)

    # Second disconnect alert should be suppressed by cooldown!
    assert len(sent_alerts) == 1

    db.close()


def test_idempotent_delivery_skips_already_delivered_source_event():
    db = TestingSessionLocal()
    now = utcnow()

    device = Device(mac="aa:bb:cc:dd:ee:05", status=DeviceStatus.TRUSTED.value)
    db.add(device)
    db.flush()

    source_event = Event(
        device_id=device.id,
        event_type="device_disconnected",
        message="Disconnected",
        created_at=now,
    )
    db.add(source_event)
    db.flush()

    sent_calls: list = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            sent_calls.append(msg)
            return True, 200, "ok", ""

    from app.notifications import PROVIDERS

    PROVIDERS["webhook"] = MockProvider()

    channel = NotificationChannel(
        name="test-channel",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/test"},
    )
    db.add(channel)
    db.commit()

    intent = AlertIntent(
        device_id=device.id,
        mac=device.mac,
        message="Alert 1",
        event_type="device_disconnected",
        source_event_id=source_event.id,
    )

    # First delivery attempt
    _deliver_queued_alerts(db, [intent])
    assert len(sent_calls) == 1

    # Second delivery attempt with same source_event_id (retry)
    _deliver_queued_alerts(db, [intent])
    # Must NOT send duplicate!
    assert len(sent_calls) == 1

    db.close()


def test_provider_failure_does_not_corrupt_device_state(monkeypatch):
    """External HTTP provider failure must never abort scan or rollback state."""
    db = TestingSessionLocal()
    now = utcnow()

    rules = BehavioralNotificationRules(notify_device_disconnected=True)
    save_behavioral_notification_rules(db, rules)

    device = Device(
        mac="aa:bb:cc:dd:ee:06",
        hostname="faulty-provider-test",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        consecutive_missed_scans=1,
        offline_candidate_since=now - timedelta(seconds=700),
    )
    db.add(device)

    class FailingProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            raise RuntimeError("Outbound network unreachable")

    db.add(
        NotificationChannel(
            name="failing-channel",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/test"},
        )
    )
    db.commit()

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": FailingProvider()})
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter([]))

    result = run_scan(db)
    assert result["success"] is True

    # Device still transitioned to offline despite provider error
    db.refresh(device)
    assert device.connectivity_state == ConnectivityState.OFFLINE.value

    # Failed alert event recorded
    fail_event = (
        db.query(Event)
        .filter(Event.device_id == device.id, Event.event_type == "alert_failed")
        .first()
    )
    assert fail_event is not None

    db.close()


def test_no_notifications_on_controller_outage(monkeypatch):
    """Controller scan failures must never generate behavioral notifications."""
    db = TestingSessionLocal()

    rules = BehavioralNotificationRules(notify_device_disconnected=True)
    save_behavioral_notification_rules(db, rules)

    device = Device(
        mac="aa:bb:cc:dd:ee:07",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
    )
    db.add(device)

    sent_alerts: list = []

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

    class FailingAdapter(UniFiAdapter):
        def get_clients(self):
            raise UniFiConnectionError("Controller down")

        def block_client(self, mac):
            return True

        def unblock_client(self, mac):
            return True

        def test_connection(self):
            return False, "Failed"

        def close(self):
            pass

    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: FailingAdapter())

    result = run_scan(db)
    assert result["success"] is False
    assert len(sent_alerts) == 0

    # Device state frozen
    db.refresh(device)
    assert device.connectivity_state == ConnectivityState.ONLINE.value

    db.close()


def test_no_notifications_on_malformed_inventory(monkeypatch):
    """Malformed inventory responses must not generate behavioral alerts."""
    db = TestingSessionLocal()

    rules = BehavioralNotificationRules(notify_device_disconnected=True)
    save_behavioral_notification_rules(db, rules)

    device = Device(
        mac="aa:bb:cc:dd:ee:08",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
    )
    db.add(device)

    sent_alerts: list = []

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
    monkeypatch.setattr("app.scanner.get_unifi_adapter", lambda: MockUniFiAdapter("invalid_string"))

    result = run_scan(db)
    assert result["success"] is False
    assert len(sent_alerts) == 0

    db.close()


# ==============================================================================
# 5. UI & Endpoint Tests
# ==============================================================================


def test_htmx_update_rules_endpoint():
    client = TestClient(app)

    # Valid POST
    resp = client.post(
        "/api/notifications/htmx/rules",
        data={
            "notify_device_disconnected": "true",
            "notify_device_reconnected": "true",
            "notify_device_ssid_changed": "false",
            "notify_device_ap_changed": "true",
            "notify_device_network_changed": "false",
            "cooldown_seconds": "7200",
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert "saved successfully" in resp.text

    # Verify persisted in DB
    db = TestingSessionLocal()
    rules = get_behavioral_notification_rules(db)
    assert rules.notify_device_disconnected is True
    assert rules.notify_device_reconnected is True
    assert rules.notify_device_ssid_changed is False
    assert rules.notify_device_ap_changed is True
    assert rules.notify_device_network_changed is False
    assert rules.cooldown_seconds == 7200

    # Audit event recorded
    event = db.query(Event).filter(Event.event_type == "notification_rules_updated").first()
    assert event is not None
    assert event.metadata_json.get("cooldown_seconds") == 7200
    db.close()

    # Invalid cooldown (negative)
    resp_invalid = client.post(
        "/api/notifications/htmx/rules",
        data={"cooldown_seconds": "-5"},
        headers=AUTH,
    )
    assert resp_invalid.status_code == 400
    assert "Cooldown must be between" in resp_invalid.text


def test_notifications_page_renders_rules_section():
    client = TestClient(app)

    db = TestingSessionLocal()
    save_behavioral_notification_rules(
        db,
        BehavioralNotificationRules(
            notify_device_disconnected=True,
            cooldown_seconds=1800,
        ),
    )
    db.commit()
    db.close()

    resp = client.get("/notifications", headers=AUTH)
    assert resp.status_code == 200
    assert "Behavioral Notification Rules" in resp.text
    assert "Device Disconnected" in resp.text
    assert "Device Reconnected" in resp.text
    assert "Alert Cooldown Window" in resp.text
    assert 'value="1800"' in resp.text
