"""Unit and integration tests for device connectivity state machine and scanner behavioral tracking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from app.config import settings
from app.db import Base
from app.models import (
    ConnectivityState,
    Device,
    DeviceStatus,
    Event,
    NotificationChannel,
    NotificationDelivery,
    Setting,
    utcnow,
)
from app.scanner import run_scan
from app.services.connectivity import (
    ConnectivityService,
    resolve_offline_thresholds,
)
from app.unifi.base import UniFiAdapter
from app.unifi.factory import close_unifi_adapter
from app.unifi.models import UniFiAuthError, UniFiConnectionError
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
def setup_db(monkeypatch):
    close_unifi_adapter()
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", False)
    monkeypatch.setattr(settings, "DEVICE_OFFLINE_MISSED_SCANS", 2)
    monkeypatch.setattr(settings, "DEVICE_OFFLINE_GRACE_SECONDS", 600)
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.main.SessionLocal", TestingSessionLocal)

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    close_unifi_adapter()


# ==============================================================================
# 1. State Machine Unit Tests
# ==============================================================================


def test_rule_a_discovered_device():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    now = utcnow()
    device = Device(mac="11:22:33:44:55:66", status=DeviceStatus.UNKNOWN.value)

    transition = service.handle_discovered_device(device, now)
    assert transition is None
    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.last_connected_at == now
    assert device.consecutive_missed_scans == 0
    assert device.offline_candidate_since is None


def test_rule_b_observed_device_already_online():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    t0 = utcnow()
    device = Device(
        mac="11:22:33:44:55:66",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_connected_at=t0,
        consecutive_missed_scans=1,
        offline_candidate_since=t0,
    )

    t1 = t0 + timedelta(minutes=5)
    transition = service.handle_observed_device(device, t1)
    assert transition is None
    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.consecutive_missed_scans == 0
    assert device.offline_candidate_since is None
    assert device.last_connected_at == t0  # Retains original connection time


def test_rule_e_reconnection_after_offline():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    t0 = utcnow()
    t_offline = t0 + timedelta(minutes=15)
    device = Device(
        id=42,
        mac="11:22:33:44:55:66",
        hostname="Laptop-Pro",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.OFFLINE.value,
        last_disconnected_at=t_offline,
        consecutive_missed_scans=3,
    )

    t_reconnect = t0 + timedelta(hours=1)
    transition = service.handle_observed_device(device, t_reconnect)

    assert transition is not None
    assert transition.event_type == "device_reconnected"
    assert transition.previous_state == ConnectivityState.OFFLINE.value
    assert transition.new_state == ConnectivityState.ONLINE.value
    assert "Laptop-Pro" in transition.event_message
    assert transition.event_metadata["mac"] == "11:22:33:44:55:66"
    assert transition.event_metadata["offline_duration_seconds"] == 2700.0

    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.last_connected_at == t_reconnect
    assert device.consecutive_missed_scans == 0
    assert device.offline_candidate_since is None


def test_rule_f_baseline_initialization_for_existing_device():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    now = utcnow()
    device = Device(
        mac="11:22:33:44:55:66",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.UNKNOWN.value,
    )

    transition = service.handle_observed_device(device, now)
    # Establishes baseline quietly without firing artificial reconnected event
    assert transition is None
    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.last_connected_at == now
    assert device.consecutive_missed_scans == 0


def test_rule_f_absent_unknown_device_remains_unknown():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    now = utcnow()
    device = Device(
        mac="11:22:33:44:55:66",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.UNKNOWN.value,
    )

    transition = service.handle_absent_device(device, now)
    assert transition is None
    assert device.connectivity_state == ConnectivityState.UNKNOWN.value
    assert device.consecutive_missed_scans == 0
    assert device.offline_candidate_since is None


def test_rule_c_device_missing_once_remains_online():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    now = utcnow()
    device = Device(
        mac="11:22:33:44:55:66",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
    )

    transition = service.handle_absent_device(device, now)
    assert transition is None
    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.consecutive_missed_scans == 1
    assert device.offline_candidate_since == now


def test_rule_d_confirmed_offline_both_conditions_met():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    t0 = utcnow()
    t_confirm = t0 + timedelta(seconds=650)
    device = Device(
        id=7,
        mac="11:22:33:44:55:66",
        hostname="Smart-TV",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        consecutive_missed_scans=1,
        offline_candidate_since=t0,
        last_connected_at=t0 - timedelta(hours=1),
    )

    transition = service.handle_absent_device(device, t_confirm)
    assert transition is not None
    assert transition.event_type == "device_disconnected"
    assert transition.previous_state == ConnectivityState.ONLINE.value
    assert transition.new_state == ConnectivityState.OFFLINE.value
    assert "Smart-TV" in transition.event_message
    assert transition.event_metadata["consecutive_missed_scans"] == 2
    assert transition.event_metadata["online_duration_seconds"] == 4250.0

    assert device.connectivity_state == ConnectivityState.OFFLINE.value
    assert device.last_disconnected_at == t_confirm
    assert device.status == DeviceStatus.TRUSTED.value  # Approval status unchanged


def test_rule_d_not_offline_if_grace_seconds_not_elapsed():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    t0 = utcnow()
    t_check = t0 + timedelta(seconds=200)  # 200s < 600s
    device = Device(
        mac="11:22:33:44:55:66",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        consecutive_missed_scans=2,  # Met missed scan count but not grace period
        offline_candidate_since=t0,
    )

    transition = service.handle_absent_device(device, t_check)
    assert transition is None
    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.consecutive_missed_scans == 3


def test_rule_d_not_offline_if_missed_scans_not_met():
    service = ConnectivityService(missed_scans_threshold=3, grace_seconds=300)
    t0 = utcnow()
    t_check = t0 + timedelta(seconds=400)  # 400s > 300s, but only 1 missed scan
    device = Device(
        mac="11:22:33:44:55:66",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        consecutive_missed_scans=0,
        offline_candidate_since=t0,
    )

    transition = service.handle_absent_device(device, t_check)
    assert transition is None
    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.consecutive_missed_scans == 1


def test_offline_device_stays_offline_no_duplicate_events():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    t0 = utcnow()
    device = Device(
        id=7,
        mac="11:22:33:44:55:66",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.OFFLINE.value,
        last_disconnected_at=t0,
        consecutive_missed_scans=5,
    )

    t_next = t0 + timedelta(minutes=5)
    transition = service.handle_absent_device(device, t_next)
    assert transition is None
    assert device.connectivity_state == ConnectivityState.OFFLINE.value
    assert device.consecutive_missed_scans == 5


def test_brief_disappearance_recovers_without_events():
    service = ConnectivityService(missed_scans_threshold=2, grace_seconds=600)
    t0 = utcnow()
    device = Device(
        mac="11:22:33:44:55:66",
        status=DeviceStatus.TRUSTED.value,
        connectivity_state=ConnectivityState.ONLINE.value,
        last_connected_at=t0,
    )

    # 1. Device missing once
    t1 = t0 + timedelta(minutes=5)
    trans1 = service.handle_absent_device(device, t1)
    assert trans1 is None
    assert device.consecutive_missed_scans == 1
    assert device.offline_candidate_since == t1

    # 2. Device returns on next scan
    t2 = t1 + timedelta(minutes=5)
    trans2 = service.handle_observed_device(device, t2)
    assert trans2 is None
    assert device.connectivity_state == ConnectivityState.ONLINE.value
    assert device.consecutive_missed_scans == 0
    assert device.offline_candidate_since is None


def test_resolve_offline_thresholds_db_override():
    db = TestingSessionLocal()
    try:
        missed, grace = resolve_offline_thresholds(db)
        assert missed == 2
        assert grace == 600

        db.add(Setting(key="device_offline_missed_scans", value="4"))
        db.add(Setting(key="device_offline_grace_seconds", value="1200"))
        db.commit()

        missed, grace = resolve_offline_thresholds(db)
        assert missed == 4
        assert grace == 1200
    finally:
        db.close()


# ==============================================================================
# 2. Scanner Integration Tests
# ==============================================================================


def test_scanner_discovers_new_device_with_online_connectivity():
    db = TestingSessionLocal()
    try:
        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = [
            {"mac": "aa:bb:cc:11:22:33", "ip": "192.168.1.100", "hostname": "Phone-New"}
        ]
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        result = run_scan(db)
        assert result["success"] is True

        device = db.query(Device).filter(Device.mac == "aa:bb:cc:11:22:33").one()
        assert device.status == DeviceStatus.UNKNOWN.value  # Approval status
        assert device.connectivity_state == ConnectivityState.ONLINE.value
        assert device.last_connected_at is not None
        assert device.consecutive_missed_scans == 0

        # Discovered event generated, no duplicate connection event
        events = db.query(Event).filter(Event.device_id == device.id).all()
        event_types = [e.event_type for e in events]
        assert "discovered" in event_types
        assert "device_reconnected" not in event_types
        assert "device_disconnected" not in event_types
    finally:
        db.close()


def test_scanner_device_transitions_to_offline_and_reconnects(monkeypatch):
    db = TestingSessionLocal()
    try:
        import app.scanner as scanner

        t0 = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(scanner, "utcnow", lambda: t0)

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = [
            {"mac": "aa:bb:cc:11:22:33", "ip": "192.168.1.100", "hostname": "Work-PC"}
        ]
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        # Scan 1: First discovery -> Online
        res1 = run_scan(db)
        assert res1["success"] is True
        d1 = db.query(Device).filter(Device.mac == "aa:bb:cc:11:22:33").one()
        assert d1.connectivity_state == ConnectivityState.ONLINE.value

        # Scan 2: Device absent at t0 + 300s -> missing once, remains online
        t1 = t0 + timedelta(seconds=300)
        monkeypatch.setattr(scanner, "utcnow", lambda: t1)
        mock_adapter.get_clients.return_value = []

        res2 = run_scan(db)
        assert res2["success"] is True
        db.refresh(d1)
        assert d1.connectivity_state == ConnectivityState.ONLINE.value
        assert d1.consecutive_missed_scans == 1
        assert d1.offline_candidate_since.replace(tzinfo=UTC) == t1

        # Scan 3: Device absent at t0 + 950s (missed=2 >= 2 and elapsed since t1 is 650s >= 600s) -> Confirmed offline
        t2 = t0 + timedelta(seconds=950)
        monkeypatch.setattr(scanner, "utcnow", lambda: t2)

        res3 = run_scan(db)
        assert res3["success"] is True
        db.refresh(d1)
        assert d1.connectivity_state == ConnectivityState.OFFLINE.value
        assert d1.consecutive_missed_scans == 2
        assert d1.last_disconnected_at.replace(tzinfo=UTC) == t2

        events = (
            db.query(Event)
            .filter(Event.device_id == d1.id, Event.event_type == "device_disconnected")
            .all()
        )
        assert len(events) == 1
        assert "Work-PC" in events[0].message
        assert events[0].metadata_json["previous_state"] == "online"
        assert events[0].metadata_json["new_state"] == "offline"

        # Scan 4: Device still absent at t0 + 1000s -> Stays offline, no duplicate event
        t3 = t0 + timedelta(seconds=1000)
        monkeypatch.setattr(scanner, "utcnow", lambda: t3)

        res4 = run_scan(db)
        assert res4["success"] is True
        events_after = (
            db.query(Event)
            .filter(Event.device_id == d1.id, Event.event_type == "device_disconnected")
            .all()
        )
        assert len(events_after) == 1

        # Scan 5: Device returns at t0 + 1500s -> Reconnected!
        t4 = t0 + timedelta(seconds=1500)
        monkeypatch.setattr(scanner, "utcnow", lambda: t4)
        mock_adapter.get_clients.return_value = [
            {"mac": "aa:bb:cc:11:22:33", "ip": "192.168.1.100", "hostname": "Work-PC"}
        ]

        res5 = run_scan(db)
        assert res5["success"] is True
        db.refresh(d1)
        assert d1.connectivity_state == ConnectivityState.ONLINE.value
        assert d1.last_connected_at.replace(tzinfo=UTC) == t4
        assert d1.consecutive_missed_scans == 0
        assert d1.offline_candidate_since is None

        reconn_events = (
            db.query(Event)
            .filter(Event.device_id == d1.id, Event.event_type == "device_reconnected")
            .all()
        )
        assert len(reconn_events) == 1
        assert reconn_events[0].metadata_json["previous_state"] == "offline"
        assert reconn_events[0].metadata_json["new_state"] == "online"

        # Scan 6: Device still online at t0 + 1800s -> Stays online, no duplicate event
        t5 = t0 + timedelta(seconds=1800)
        monkeypatch.setattr(scanner, "utcnow", lambda: t5)

        res6 = run_scan(db)
        assert res6["success"] is True
        reconn_events_after = (
            db.query(Event)
            .filter(Event.device_id == d1.id, Event.event_type == "device_reconnected")
            .all()
        )
        assert len(reconn_events_after) == 1
    finally:
        db.close()


def test_scanner_approval_status_preserved_across_connectivity_transitions(monkeypatch):
    db = TestingSessionLocal()
    try:
        import app.scanner as scanner

        t0 = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(scanner, "utcnow", lambda: t0)

        # Seed devices with various approval statuses
        dev_trusted = Device(
            mac="11:11:11:11:11:11",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
        )
        dev_ignored = Device(
            mac="22:22:22:22:22:22",
            status=DeviceStatus.IGNORED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
        )
        dev_blocked = Device(
            mac="33:33:33:33:33:33",
            status=DeviceStatus.BLOCKED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
        )
        db.add_all([dev_trusted, dev_ignored, dev_blocked])
        db.commit()

        # Run scan where none are present -> absent transition
        t1 = t0 + timedelta(seconds=700)
        monkeypatch.setattr(scanner, "utcnow", lambda: t1)

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = []
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        # Run 2 scans to meet threshold
        run_scan(db)
        monkeypatch.setattr(scanner, "utcnow", lambda: t1 + timedelta(seconds=650))
        run_scan(db)

        db.refresh(dev_trusted)
        db.refresh(dev_ignored)
        db.refresh(dev_blocked)

        assert dev_trusted.status == DeviceStatus.TRUSTED.value
        assert dev_trusted.connectivity_state == ConnectivityState.OFFLINE.value

        assert dev_ignored.status == DeviceStatus.IGNORED.value
        assert dev_ignored.connectivity_state == ConnectivityState.OFFLINE.value

        assert dev_blocked.status == DeviceStatus.BLOCKED.value
        assert dev_blocked.connectivity_state == ConnectivityState.OFFLINE.value
    finally:
        db.close()


def test_scanner_outage_does_not_modify_connectivity_state():
    db = TestingSessionLocal()
    try:
        t0 = utcnow()
        dev = Device(
            mac="11:22:33:44:55:66",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=1,
            offline_candidate_since=t0,
        )
        db.add(dev)
        db.commit()

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.side_effect = UniFiConnectionError(
            "Connection refused to controller"
        )
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        result = run_scan(db)
        assert result["success"] is False

        # State and counters MUST remain completely unchanged
        db.refresh(dev)
        assert dev.connectivity_state == ConnectivityState.ONLINE.value
        assert dev.consecutive_missed_scans == 1
        assert dev.offline_candidate_since.replace(tzinfo=UTC) == t0

        # Scan failed event recorded
        fail_event = db.query(Event).filter(Event.event_type == "scan_failed").one()
        assert "Connection refused" in fail_event.message
    finally:
        db.close()


def test_scanner_auth_error_does_not_modify_connectivity_state():
    db = TestingSessionLocal()
    try:
        dev = Device(
            mac="11:22:33:44:55:66",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=0,
        )
        db.add(dev)
        db.commit()

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.side_effect = UniFiAuthError("Invalid API credentials (HTTP 401)")
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        result = run_scan(db)
        assert result["success"] is False

        db.refresh(dev)
        assert dev.connectivity_state == ConnectivityState.ONLINE.value
        assert dev.consecutive_missed_scans == 0
    finally:
        db.close()


def test_scanner_no_behavioral_notifications_sent(monkeypatch):
    """Behavioral events are stored in the database but must NOT dispatch notifications."""
    db = TestingSessionLocal()
    try:
        import app.scanner as scanner

        t0 = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(scanner, "utcnow", lambda: t0)

        # Mock notification provider
        send_calls = []
        mock_provider = MagicMock()
        mock_provider.validate_config.return_value = True
        mock_provider.send.side_effect = lambda *args, **kwargs: send_calls.append(args)
        monkeypatch.setattr(scanner, "PROVIDERS", {"webhook": mock_provider})

        # Add enabled notification channel
        channel = NotificationChannel(
            type="webhook",
            name="Test Webhook",
            enabled=True,
            config_json={"url": "https://example.com/webhook"},
        )
        db.add(channel)

        # Trusted device online
        dev = Device(
            mac="11:22:33:44:55:66",
            hostname="Trusted-Device",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=1,
            offline_candidate_since=t0,
        )
        db.add(dev)
        db.commit()

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = []
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        # Run scan at t0 + 700s -> triggers device_disconnected
        t1 = t0 + timedelta(seconds=700)
        monkeypatch.setattr(scanner, "utcnow", lambda: t1)

        result = run_scan(db)
        assert result["success"] is True

        # Confirm disconnected event exists
        event = (
            db.query(Event)
            .filter(Event.device_id == dev.id, Event.event_type == "device_disconnected")
            .first()
        )
        assert event is not None

        # Verify no notification deliveries were created or sent for behavioral events
        deliveries = db.query(NotificationDelivery).all()
        assert len(deliveries) == 0
        assert len(send_calls) == 0
    finally:
        db.close()


def test_scanner_rejects_none_clients_data_as_scan_failed(monkeypatch):
    """Finding 1: None returned from get_clients must produce scan_failed without advancing missed-scans."""
    db = TestingSessionLocal()
    try:
        t0 = utcnow()
        dev = Device(
            mac="11:22:33:44:55:66",
            hostname="Online-Device",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=1,
            offline_candidate_since=t0,
        )
        db.add(dev)
        db.commit()

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = None
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        result = run_scan(db)
        assert result["success"] is False
        assert "expected list, got None" in result["message"]

        # Confirm scan_failed event recorded
        failed_event = db.query(Event).filter(Event.event_type == "scan_failed").first()
        assert failed_event is not None
        assert "expected list, got None" in failed_event.message

        # Verify device state and counters remain untouched
        db.refresh(dev)
        assert dev.connectivity_state == ConnectivityState.ONLINE.value
        assert dev.consecutive_missed_scans == 1
        assert dev.offline_candidate_since.replace(tzinfo=UTC) == t0
    finally:
        db.close()


def test_scanner_rejects_non_list_clients_data(monkeypatch):
    """Finding 1: Non-list returned from get_clients must produce scan_failed."""
    db = TestingSessionLocal()
    try:
        dev = Device(
            mac="11:22:33:44:55:77",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=0,
        )
        db.add(dev)
        db.commit()

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = {"clients": []}
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        result = run_scan(db)
        assert result["success"] is False
        assert "expected list, got dict" in result["message"]

        db.refresh(dev)
        assert dev.connectivity_state == ConnectivityState.ONLINE.value
        assert dev.consecutive_missed_scans == 0
    finally:
        db.close()


def test_scanner_rejects_malformed_client_entry(monkeypatch):
    """Finding 1: Malformed entries in clients_data produce scan_failed without modifying devices."""
    db = TestingSessionLocal()
    try:
        dev = Device(
            mac="11:22:33:44:55:88",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=0,
        )
        db.add(dev)
        db.commit()

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = ["not-a-mapping-or-dict"]
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        result = run_scan(db)
        assert result["success"] is False
        assert "malformed device entry" in result["message"]

        db.refresh(dev)
        assert dev.connectivity_state == ConnectivityState.ONLINE.value
        assert dev.consecutive_missed_scans == 0
    finally:
        db.close()


def test_scanner_reconciliation_failure_rolls_back_partial_changes(monkeypatch):
    """Finding 2: Errors during reconciliation roll back all partial device modifications."""
    db = TestingSessionLocal()
    try:
        dev1 = Device(
            mac="11:22:33:44:55:11",
            hostname="Device-1",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=0,
        )
        dev2 = Device(
            mac="11:22:33:44:55:22",
            hostname="Device-2",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=0,
        )
        db.add_all([dev1, dev2])
        db.commit()

        # Mock adapter with an empty client list so both devices enter absent pass
        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = []
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        # Inject an error when handling dev2 in the absent pass
        original_handle_absent = ConnectivityService.handle_absent_device
        call_count = 0

        def flaky_handle_absent(self, device, now):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated unhandled reconciliation failure on device 2")
            return original_handle_absent(self, device, now)

        monkeypatch.setattr(ConnectivityService, "handle_absent_device", flaky_handle_absent)

        result = run_scan(db)
        assert result["success"] is False
        assert "Scan failed during reconciliation" in result["message"]
        assert "Simulated unhandled reconciliation failure" in result["message"]

        # Confirm scan_failed event was recorded on a best-effort basis
        failed_event = (
            db.query(Event)
            .filter(
                Event.event_type == "scan_failed",
                Event.message.contains("Simulated unhandled reconciliation failure"),
            )
            .first()
        )
        assert failed_event is not None

        # Verify that dev1's modification was rolled back and NOT committed
        db.refresh(dev1)
        db.refresh(dev2)
        assert dev1.consecutive_missed_scans == 0
        assert dev1.connectivity_state == ConnectivityState.ONLINE.value
        assert dev2.consecutive_missed_scans == 0
        assert dev2.connectivity_state == ConnectivityState.ONLINE.value
    finally:
        db.close()


def test_scanner_evaluates_only_online_devices_in_absence_pass(monkeypatch):
    """Finding 4: Absence pass queries only online devices; offline and unknown are not scanned or incremented."""
    db = TestingSessionLocal()
    try:
        t0 = utcnow()
        dev_online = Device(
            mac="11:22:33:44:55:01",
            hostname="Device-Online",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.ONLINE.value,
            consecutive_missed_scans=0,
        )
        dev_offline = Device(
            mac="11:22:33:44:55:02",
            hostname="Device-Offline",
            status=DeviceStatus.TRUSTED.value,
            connectivity_state=ConnectivityState.OFFLINE.value,
            consecutive_missed_scans=5,
            last_disconnected_at=t0 - timedelta(hours=2),
        )
        dev_unknown = Device(
            mac="11:22:33:44:55:03",
            hostname="Device-Unknown",
            status=DeviceStatus.UNKNOWN.value,
            connectivity_state=ConnectivityState.UNKNOWN.value,
            consecutive_missed_scans=0,
        )
        db.add_all([dev_online, dev_offline, dev_unknown])
        db.commit()

        mock_adapter = MagicMock(spec=UniFiAdapter)
        mock_adapter.get_clients.return_value = []
        import app.unifi.factory as factory

        factory._shared_adapter = mock_adapter

        result = run_scan(db)
        assert result["success"] is True

        db.refresh(dev_online)
        db.refresh(dev_offline)
        db.refresh(dev_unknown)

        # dev_online was absent so its missed scans incremented
        assert dev_online.consecutive_missed_scans == 1

        # dev_offline was filtered out of absence query; missed scans remains 5
        assert dev_offline.consecutive_missed_scans == 5

        # dev_unknown was filtered out of absence query; missed scans remains 0
        assert dev_unknown.consecutive_missed_scans == 0
    finally:
        db.close()
