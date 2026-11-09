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
from app.scanner import (
    AlertIntent,
    _deliver_pending_alerts,
    _deliver_queued_alerts,
    _devices_in_alert_cooldown,
    run_scan,
)
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


def test_per_channel_retry_and_deduplication(monkeypatch):
    """If one channel fails while another succeeds, retry only attempts the failed channel."""
    db = TestingSessionLocal()
    now = utcnow()

    device = Device(mac="aa:bb:cc:11:22:33", hostname="multi-channel-device")
    db.add(device)
    source_event = Event(device_id=device.id, event_type="device_disconnected", created_at=now)
    db.add(source_event)
    db.commit()

    ch1 = NotificationChannel(
        name="channel-1-success",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/ch1"},
    )
    ch2 = NotificationChannel(
        name="channel-2-flaky",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/ch2"},
    )
    db.add_all([ch1, ch2])
    db.commit()

    ch2_should_fail = True
    ch1_calls: list[str] = []
    ch2_calls: list[str] = []

    class MockMultiProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            url = cfg.get("url")
            if "ch1" in url:
                ch1_calls.append(msg)
                return True, 200, "ok", ""
            if "ch2" in url:
                ch2_calls.append(msg)
                if ch2_should_fail:
                    return False, 500, "internal error", "Server error"
                return True, 200, "recovered", ""
            return False, 0, "", "unknown"

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockMultiProvider()})

    intent = AlertIntent(
        device_id=device.id,
        mac=device.mac,
        message="Device disconnected alert",
        event_type="device_disconnected",
        source_event_id=source_event.id,
    )

    # First pass: ch1 succeeds, ch2 fails
    _deliver_queued_alerts(db, [intent])
    assert len(ch1_calls) == 1
    assert len(ch2_calls) == 1

    # Verify deliveries in DB
    delivs = db.query(NotificationDelivery).all()
    assert len(delivs) == 2
    d1 = next(d for d in delivs if d.channel_id == ch1.id)
    d2 = next(d for d in delivs if d.channel_id == ch2.id)
    assert d1.success is True
    assert d2.success is False

    # Second pass (retry): ch2 recovers
    ch2_should_fail = False
    _deliver_pending_alerts(db)

    # ch1 must NOT be called again (deduplicated per channel!)
    assert len(ch1_calls) == 1
    # ch2 WAS called again and succeeded!
    assert len(ch2_calls) == 2

    delivs_after = db.query(NotificationDelivery).all()
    assert len(delivs_after) == 3
    d2_retry = [d for d in delivs_after if d.channel_id == ch2.id and d.success is True]
    assert len(d2_retry) == 1

    # Third pass: everything already succeeded, nothing to do
    _deliver_pending_alerts(db)
    assert len(ch1_calls) == 1
    assert len(ch2_calls) == 2
    db.close()


def test_delivery_budget_and_batch_limits(monkeypatch):
    """Delivery runner respects batch limits and defers remaining queued alerts."""
    db = TestingSessionLocal()
    now = utcnow()

    sent_calls: list[str] = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            sent_calls.append(msg)
            return True, 200, "ok", ""

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})

    channel = NotificationChannel(
        name="test-batch-channel",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/batch"},
    )
    db.add(channel)

    # Create 5 queued alert events directly in DB
    for i in range(5):
        dev = Device(mac=f"00:11:22:33:44:{i:02d}", hostname=f"dev-{i}")
        db.add(dev)
        db.flush()
        db.add(
            Event(
                device_id=dev.id,
                event_type="alert_queued",
                message=f"Alert for dev {i}",
                metadata_json={"source_event_id": 100 + i, "event_type": "device_disconnected"},
                created_at=now + timedelta(seconds=i),
            )
        )
    db.commit()

    # Run with batch_limit=2
    attempted = _deliver_pending_alerts(db, batch_limit=2)
    assert attempted == 2
    assert len(sent_calls) == 2

    # Run again with batch_limit=2 -> next 2 processed
    attempted_2 = _deliver_pending_alerts(db, batch_limit=2)
    assert attempted_2 == 2
    assert len(sent_calls) == 4

    # Run again with batch_limit=2 -> final 1 processed
    attempted_3 = _deliver_pending_alerts(db, batch_limit=2)
    assert attempted_3 == 1
    assert len(sent_calls) == 5

    # Run again -> nothing remaining
    attempted_4 = _deliver_pending_alerts(db, batch_limit=2)
    assert attempted_4 == 0
    assert len(sent_calls) == 5
    db.close()


def test_durable_recovery_after_process_restart(monkeypatch):
    """Pending alert_queued events in SQLite are cleanly delivered even without in-memory scan state."""
    db = TestingSessionLocal()
    now = utcnow()

    delivered: list[str] = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            delivered.append(msg)
            return True, 200, "ok", ""

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})

    channel = NotificationChannel(
        name="durable-channel",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/durable"},
    )
    dev = Device(mac="aa:bb:cc:99:88:77", hostname="persisted-alert-dev")
    db.add_all([channel, dev])
    db.flush()

    # Simulate an alert_queued event that was committed before a crash/restart
    db.add(
        Event(
            device_id=dev.id,
            event_type="alert_queued",
            message="Persisted alert before crash",
            metadata_json={"source_event_id": 999, "event_type": "device_disconnected"},
            created_at=now,
        )
    )
    db.commit()

    # Recovery pass (e.g. at startup or on next scan pass)
    _deliver_pending_alerts(db)

    assert len(delivered) == 1
    assert delivered[0] == "Persisted alert before crash"

    deliv = db.query(NotificationDelivery).first()
    assert deliv is not None
    assert deliv.success is True
    assert deliv.channel_id == channel.id
    db.close()


def test_starvation_prevention_over_50_completed_events(monkeypatch):
    """Old completed alert_queued records cannot starve newer pending work even when >50 exist."""
    db = TestingSessionLocal()
    now = utcnow()
    sent_calls: list[str] = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            sent_calls.append(msg)
            return True, 200, "ok", ""

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})

    channel = NotificationChannel(
        name="starvation-test-channel",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/starvation"},
    )
    db.add(channel)
    db.flush()

    # Create 55 completed alert_queued events (older timestamps)
    # Each has a successful NotificationDelivery for the channel and resolved=True
    for i in range(55):
        dev = Device(mac=f"00:22:33:44:55:{i:02x}", hostname=f"completed-dev-{i}")
        db.add(dev)
        db.flush()
        q_event = Event(
            device_id=dev.id,
            event_type="alert_queued",
            message=f"Completed alert {i}",
            metadata_json={
                "source_event_id": 200 + i,
                "event_type": "device_disconnected",
                "resolved": True,
            },
            created_at=now - timedelta(hours=10) + timedelta(seconds=i),
        )
        db.add(q_event)
        db.flush()
        db.add(
            NotificationDelivery(
                event_id=q_event.id,
                channel_id=channel.id,
                success=True,
                status_code=200,
                response="ok",
                created_at=q_event.created_at,
            )
        )

    # Create 5 newer pending alert_queued events
    pending_ids = []
    for j in range(5):
        dev = Device(mac=f"00:77:88:99:aa:{j:02x}", hostname=f"pending-dev-{j}")
        db.add(dev)
        db.flush()
        q_event = Event(
            device_id=dev.id,
            event_type="alert_queued",
            message=f"Pending alert {j}",
            metadata_json={
                "source_event_id": 500 + j,
                "event_type": "device_disconnected",
                "resolved": False,
            },
            created_at=now - timedelta(minutes=5) + timedelta(seconds=j),
        )
        db.add(q_event)
        db.flush()
        pending_ids.append(q_event.id)
    db.commit()

    # If completed events starved the query, the .limit(50) on queued_events would return
    # only the first 50 completed records, resulting in 0 attempted deliveries.
    # With pending-work-aware query, all 5 pending events are fetched and delivered.
    attempted = _deliver_pending_alerts(db, batch_limit=10)
    assert attempted == 5
    assert len(sent_calls) == 5
    assert sent_calls == [f"Pending alert {j}" for j in range(5)]

    # Verify all 5 pending events are now marked resolved
    delivered_events = db.query(Event).filter(Event.id.in_(pending_ids)).all()
    for ev in delivered_events:
        assert ev.metadata_json.get("resolved") is True

    db.close()


def test_pending_delivery_recovery_after_24_hours(monkeypatch):
    """Pending alerts between 24h and 48h old are not discarded and are recovered successfully."""
    db = TestingSessionLocal()
    now = utcnow()
    sent_calls: list[str] = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            sent_calls.append(msg)
            return True, 200, "ok", ""

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})

    channel = NotificationChannel(
        name="recovery-channel",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/recovery"},
    )
    dev = Device(mac="11:22:33:44:55:66", hostname="recovering-dev")
    db.add_all([channel, dev])
    db.flush()

    # Event queued 30 hours ago (beyond old 24h cutoff, but within 48h expiration)
    q_event = Event(
        device_id=dev.id,
        event_type="alert_queued",
        message="Alert from 30 hours ago",
        metadata_json={"source_event_id": 888, "event_type": "device_disconnected"},
        created_at=now - timedelta(hours=30),
    )
    db.add(q_event)
    db.commit()

    # Deliver pending alerts: should recover and deliver
    attempted = _deliver_pending_alerts(db)
    assert attempted == 1
    assert len(sent_calls) == 1
    assert sent_calls[0] == "Alert from 30 hours ago"

    # Verify delivery record exists and succeeded
    delivery = db.query(NotificationDelivery).filter_by(event_id=q_event.id).first()
    assert delivery is not None
    assert delivery.success is True
    assert delivery.channel_id == channel.id

    db.refresh(q_event)
    assert q_event.metadata_json.get("resolved") is True
    assert q_event.metadata_json.get("expired") is not True
    db.close()


def test_explicit_expiration_policy_after_48_hours(monkeypatch):
    """Alerts exceeding expiration window (>48h) are not silently dropped; terminal failure is recorded."""
    db = TestingSessionLocal()
    now = utcnow()
    sent_calls: list[str] = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            sent_calls.append(msg)
            return True, 200, "ok", ""

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})

    channel = NotificationChannel(
        name="expiration-channel",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/expiration"},
    )
    dev = Device(mac="11:22:33:44:55:77", hostname="expired-dev")
    db.add_all([channel, dev])
    db.flush()

    # Event queued 50 hours ago (exceeding 48h expiration)
    q_event = Event(
        device_id=dev.id,
        event_type="alert_queued",
        message="Alert from 50 hours ago",
        metadata_json={"source_event_id": 889, "event_type": "device_disconnected"},
        created_at=now - timedelta(hours=50),
    )
    db.add(q_event)
    db.commit()

    attempted = _deliver_pending_alerts(db)
    # No actual provider attempts because it expired
    assert attempted == 0
    assert len(sent_calls) == 0

    # Explicit failure delivery written to DB
    delivery = db.query(NotificationDelivery).filter_by(event_id=q_event.id).first()
    assert delivery is not None
    assert delivery.success is False
    assert "Alert expired" in (delivery.error or "")

    # Audit event recorded
    failed_event = (
        db.query(Event)
        .filter(Event.device_id == dev.id, Event.event_type == "alert_failed")
        .first()
    )
    assert failed_event is not None
    assert failed_event.severity == "error"
    assert failed_event.metadata_json.get("reason") == "expired"

    # Queued event marked resolved and expired
    db.refresh(q_event)
    assert q_event.metadata_json.get("resolved") is True
    assert q_event.metadata_json.get("expired") is True
    db.close()


def test_batch_ending_midway_through_multichannel_event(monkeypatch):
    """A batch limit stopping midway through a multi-channel event commits channel 1 and defers channel 2."""
    db = TestingSessionLocal()
    now = utcnow()
    calls: list[tuple[str, str]] = []

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            ch_name = cfg.get("name", "unknown")
            calls.append((ch_name, msg))
            return True, 200, "ok", ""

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})

    ch1 = NotificationChannel(
        name="channel-1",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/1", "name": "channel-1"},
    )
    ch2 = NotificationChannel(
        name="channel-2",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/2", "name": "channel-2"},
    )
    dev = Device(mac="22:33:44:55:66:77", hostname="midway-dev")
    db.add_all([ch1, ch2, dev])
    db.flush()

    q_event = Event(
        device_id=dev.id,
        event_type="alert_queued",
        message="Midway multi-channel alert",
        metadata_json={"source_event_id": 901, "event_type": "device_disconnected"},
        created_at=now,
    )
    db.add(q_event)
    db.commit()

    # Pass 1: batch_limit = 1
    # Only 1 delivery attempt should run
    attempted_1 = _deliver_pending_alerts(db, batch_limit=1)
    assert attempted_1 == 1
    assert len(calls) == 1
    assert calls[0][0] == "channel-1"

    # Verify channel 1 delivery is durably committed in DB
    delivs_1 = db.query(NotificationDelivery).filter_by(event_id=q_event.id).all()
    assert len(delivs_1) == 1
    assert delivs_1[0].channel_id == ch1.id
    assert delivs_1[0].success is True

    # q_event should NOT be resolved yet because channel 2 is still pending
    db.refresh(q_event)
    assert q_event.metadata_json.get("resolved") is not True

    # Pass 2: batch_limit = 1
    # Channel 2 should run now, and channel 1 should NOT be re-called
    attempted_2 = _deliver_pending_alerts(db, batch_limit=1)
    assert attempted_2 == 1
    assert len(calls) == 2
    assert calls[1][0] == "channel-2"

    delivs_2 = db.query(NotificationDelivery).filter_by(event_id=q_event.id).all()
    assert len(delivs_2) == 2
    ch_ids = {d.channel_id for d in delivs_2}
    assert ch_ids == {ch1.id, ch2.id}

    # Now q_event should be resolved
    db.refresh(q_event)
    assert q_event.metadata_json.get("resolved") is True

    db.close()


def test_cooldown_and_deduplication_after_partial_batch(monkeypatch):
    """Cooldown and deduplication are preserved even after a partial batch where alert_sent is not complete."""
    db = TestingSessionLocal()
    now = utcnow()

    class MockProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            return True, 200, "ok", ""

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockProvider()})

    ch1 = NotificationChannel(
        name="cooldown-ch1",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/c1"},
    )
    ch2 = NotificationChannel(
        name="cooldown-ch2",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/c2"},
    )
    dev = Device(mac="33:44:55:66:77:88", hostname="cooldown-dev")
    db.add_all([ch1, ch2, dev])
    db.flush()

    q_event = Event(
        device_id=dev.id,
        event_type="alert_queued",
        message="Cooldown test alert",
        metadata_json={"source_event_id": 902, "event_type": "device_disconnected"},
        created_at=now,
    )
    db.add(q_event)
    db.commit()

    # Run pass with batch_limit=1 -> channel 1 succeeds, channel 2 deferred
    _deliver_pending_alerts(db, batch_limit=1)

    # Cooldown checks must immediately recognize the device as in cooldown:
    # 1. Behavioral cooldown check via is_in_behavioral_cooldown
    assert is_in_behavioral_cooldown(db, dev.id, "device_disconnected", 3600, now) is True

    # 2. Behavioral batch check via batch_devices_in_cooldown
    cooldown_pairs = batch_devices_in_cooldown(db, [(dev.id, "device_disconnected")], 3600, now)
    assert (dev.id, "device_disconnected") in cooldown_pairs

    # A different event type (e.g. device_reconnected) is NOT suppressed
    assert is_in_behavioral_cooldown(db, dev.id, "device_reconnected", 3600, now) is False
    assert (dev.id, "device_reconnected") not in batch_devices_in_cooldown(
        db, [(dev.id, "device_reconnected")], 3600, now
    )

    # 3. Discovery cooldown check via scanner._devices_in_alert_cooldown for discovery events
    dev_disc = Device(mac="33:44:55:66:77:99", hostname="disc-dev")
    db.add(dev_disc)
    db.flush()
    q_disc = Event(
        device_id=dev_disc.id,
        event_type="alert_queued",
        message="Discovery alert",
        metadata_json={"event_type": "discovered"},
        created_at=now,
    )
    db.add(q_disc)
    db.commit()

    _deliver_pending_alerts(db, batch_limit=1)
    disc_cooldown = _devices_in_alert_cooldown(db, [dev_disc.id], now)
    assert dev_disc.id in disc_cooldown

    db.close()


def test_provider_call_exceeds_remaining_delivery_budget(monkeypatch):
    """When a delivery provider call consumes the time budget, subsequent deliveries are deferred."""
    db = TestingSessionLocal()
    now = utcnow()
    calls: list[str] = []

    # Mock time.monotonic to simulate elapsed wall-clock budget
    current_time = [100.0]

    def mock_monotonic():
        return current_time[0]

    monkeypatch.setattr("time.monotonic", mock_monotonic)

    class MockSlowProvider:
        def validate_config(self, cfg):
            return True

        def send(self, msg, cfg):
            calls.append(cfg.get("url", ""))
            # Simulate request taking 0.35s
            current_time[0] += 0.35
            return True, 200, "ok", ""

    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": MockSlowProvider()})

    ch1 = NotificationChannel(
        name="budget-ch1",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/b1"},
    )
    ch2 = NotificationChannel(
        name="budget-ch2",
        type="webhook",
        enabled=True,
        config_json={"url": "https://hooks.example.com/b2"},
    )
    dev = Device(mac="44:55:66:77:88:99", hostname="budget-dev")
    db.add_all([ch1, ch2, dev])
    db.flush()

    q_event = Event(
        device_id=dev.id,
        event_type="alert_queued",
        message="Budget test alert",
        metadata_json={"source_event_id": 903, "event_type": "device_disconnected"},
        created_at=now,
    )
    db.add(q_event)
    db.commit()

    # budget_seconds = 0.3s.
    # Delivery 1 starts at t=100.0, finishes at t=100.35.
    # Before Delivery 2, remaining_budget = (100.0 + 0.3) - 100.35 = -0.05 <= 0.
    # Delivery 2 must be deferred!
    attempted = _deliver_pending_alerts(db, budget_seconds=0.3)
    assert attempted == 1
    assert len(calls) == 1
    assert calls[0] == "https://hooks.example.com/b1"

    # Only 1 delivery was recorded
    delivs = db.query(NotificationDelivery).filter_by(event_id=q_event.id).all()
    assert len(delivs) == 1
    assert delivs[0].channel_id == ch1.id

    # The event remains unresolved for subsequent scans
    db.refresh(q_event)
    assert q_event.metadata_json.get("resolved") is not True

    # Advance time / reset budget for next scan: delivery 2 completes
    attempted_2 = _deliver_pending_alerts(db, budget_seconds=0.5)
    assert attempted_2 == 1
    assert len(calls) == 2
    assert calls[1] == "https://hooks.example.com/b2"

    db.refresh(q_event)
    assert q_event.metadata_json.get("resolved") is True
    db.close()
