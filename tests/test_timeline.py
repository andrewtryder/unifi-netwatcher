"""Unit and integration tests for device connection history and timeline UI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db import Base, get_db
from app.main import app
from app.models import Device, DeviceStatus, Event
from app.security.service import invalidate_security_cache
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


def test_timeline_chronological_ordering_and_filtering():
    """Timeline queries must strictly filter by device_id and order newest to oldest."""
    db = TestingSessionLocal()
    t0 = datetime(2026, 11, 9, 10, 0, 0, tzinfo=UTC)

    dev1 = Device(mac="11:11:11:11:11:11", hostname="dev-1", status=DeviceStatus.TRUSTED.value)
    dev2 = Device(mac="22:22:22:22:22:22", hostname="dev-2", status=DeviceStatus.TRUSTED.value)
    db.add_all([dev1, dev2])
    db.flush()

    ev1_dev1 = Event(
        device_id=dev1.id,
        event_type="discovered",
        message="Dev 1 discovered",
        created_at=t0,
    )
    ev2_dev1 = Event(
        device_id=dev1.id,
        event_type="device_disconnected",
        message="Dev 1 disconnected",
        created_at=t0 + timedelta(hours=1),
    )
    ev3_dev1 = Event(
        device_id=dev1.id,
        event_type="device_reconnected",
        message="Dev 1 reconnected",
        created_at=t0 + timedelta(hours=2),
    )
    ev_dev2 = Event(
        device_id=dev2.id,
        event_type="device_disconnected",
        message="Dev 2 disconnected",
        created_at=t0 + timedelta(hours=3),
    )
    db.add_all([ev1_dev1, ev2_dev1, ev3_dev1, ev_dev2])
    db.commit()

    client = TestClient(app)
    resp = client.get(f"/devices/{dev1.id}", headers=AUTH)
    assert resp.status_code == 200

    html = resp.text
    assert "Dev 1 reconnected" in html
    assert "Dev 1 disconnected" in html
    assert "Dev 1 discovered" in html
    # Device 2's event must NOT appear in Device 1's timeline!
    assert "Dev 2 disconnected" not in html

    # Chronological check: newest event appears before older events in HTML
    pos_reconnected = html.find("Dev 1 reconnected")
    pos_disconnected = html.find("Dev 1 disconnected")
    pos_discovered = html.find("Dev 1 discovered")
    assert pos_reconnected < pos_disconnected < pos_discovered

    db.close()


def test_timeline_bounded_limit_and_pagination():
    """Initial load must limit to 30 items; HTMX endpoint pages remaining."""
    db = TestingSessionLocal()
    t0 = datetime(2026, 11, 9, 10, 0, 0, tzinfo=UTC)

    dev = Device(mac="33:33:33:33:33:33", hostname="busy-device", status=DeviceStatus.TRUSTED.value)
    db.add(dev)
    db.flush()

    # Create 45 events
    events = [
        Event(
            device_id=dev.id,
            event_type="device_ap_changed",
            message=f"Roamed transition #{i}",
            created_at=t0 + timedelta(minutes=i),
        )
        for i in range(45)
    ]
    db.add_all(events)
    db.commit()

    client = TestClient(app)

    # 1. Page load gets first 30 events
    resp = client.get(f"/devices/{dev.id}", headers=AUTH)
    assert resp.status_code == 200
    assert "Roamed transition #44" in resp.text
    assert "Roamed transition #15" in resp.text
    # Event #14 (which is item 31) should not be on first page
    assert "Roamed transition #14" not in resp.text
    assert "Load more activity" in resp.text
    assert 'offset=30"' in resp.text

    # 2. HTMX call to fetch offset=30 gets remaining 15 events
    resp_page2 = client.get(f"/htmx/devices/{dev.id}/timeline?offset=30&limit=30", headers=AUTH)
    assert resp_page2.status_code == 200
    assert "Roamed transition #14" in resp_page2.text
    assert "Roamed transition #0" in resp_page2.text
    # Last page: no further "Load more" button
    assert "Load more activity" not in resp_page2.text

    db.close()


def test_timeline_renders_event_types_and_durations():
    """Timeline properly displays badges, durations, and state differences."""
    db = TestingSessionLocal()
    t0 = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)

    dev = Device(
        mac="44:44:44:44:44:44", hostname="display-tester", status=DeviceStatus.TRUSTED.value
    )
    db.add(dev)
    db.flush()

    # Disconnect with duration
    ev_disc = Event(
        device_id=dev.id,
        event_type="device_disconnected",
        message="Device disconnected: display-tester",
        metadata_json={
            "online_duration_seconds": 3660,
            "previous_state": "online",
            "new_state": "offline",
        },
        created_at=t0,
    )
    # Reconnect with duration
    ev_rec = Event(
        device_id=dev.id,
        event_type="device_reconnected",
        message="Device reconnected: display-tester",
        metadata_json={
            "offline_duration_seconds": 720,
            "previous_state": "offline",
            "new_state": "online",
        },
        created_at=t0 + timedelta(minutes=12),
    )
    # Roaming with old and new AP
    ev_roam = Event(
        device_id=dev.id,
        event_type="device_ap_changed",
        message="Device roamed AP",
        metadata_json={
            "old_ap_mac": "11:22:33:44:55:01",
            "new_ap_mac": "11:22:33:44:55:02",
        },
        created_at=t0 + timedelta(minutes=30),
    )
    # SSID change
    ev_ssid = Event(
        device_id=dev.id,
        event_type="device_ssid_changed",
        message="Device SSID changed",
        metadata_json={
            "old_ssid": "Corp-Guest",
            "new_ssid": "Corp-Secure",
        },
        created_at=t0 + timedelta(minutes=45),
    )
    # IP change
    ev_net = Event(
        device_id=dev.id,
        event_type="device_network_changed",
        message="Device IP changed",
        metadata_json={
            "old_ip": "10.0.0.10",
            "new_ip": "10.0.0.25",
        },
        created_at=t0 + timedelta(minutes=50),
    )
    db.add_all([ev_disc, ev_rec, ev_roam, ev_ssid, ev_net])
    db.commit()

    client = TestClient(app)
    resp = client.get(f"/devices/{dev.id}", headers=AUTH)
    assert resp.status_code == 200

    html = resp.text
    # Badges
    assert "Disconnected" in html
    assert "Reconnected" in html
    assert "AP Roamed" in html
    assert "SSID Changed" in html
    assert "IP Changed" in html

    # Duration pills
    assert "Online: 1h 1m" in html
    assert "Offline: 12m" in html

    # Transition differences
    assert "11:22:33:44:55:01 &rarr; 11:22:33:44:55:02" in html
    assert "Corp-Guest &rarr; Corp-Secure" in html
    assert "10.0.0.10 &rarr; 10.0.0.25" in html

    db.close()


def test_timeline_xss_escaping_of_untrusted_metadata():
    """All user-controlled or device-supplied event strings must be safely HTML escaped."""
    db = TestingSessionLocal()
    dev = Device(mac="55:55:55:55:55:55", hostname="xss-test", status=DeviceStatus.TRUSTED.value)
    db.add(dev)
    db.flush()

    malicious_event = Event(
        device_id=dev.id,
        event_type="device_ssid_changed",
        message="<script>alert('pwned_message')</script>",
        metadata_json={
            "old_ssid": "<img src=x onerror=alert('xss_ssid')>",
            "new_ssid": "<b>safe</b>",
            "old_ip": "192.168.1.1<script>",
            "new_ip": "192.168.1.2",
        },
        created_at=datetime.now(UTC),
    )
    db.add(malicious_event)
    db.commit()

    client = TestClient(app)
    resp = client.get(f"/devices/{dev.id}", headers=AUTH)
    assert resp.status_code == 200

    html = resp.text
    # Raw script or img tags from malicious inputs must NEVER appear unescaped!
    assert "<script>alert" not in html
    assert "<img src=x" not in html
    assert "<b>safe</b>" not in html
    assert "&lt;script&gt;alert" in html
    assert "&lt;img src=x" in html
    assert "&lt;b&gt;safe&lt;/b&gt;" in html
    assert "pwned_message" in html

    db.close()


def test_timeline_empty_history_renders_empty_state():
    """Device without connection events renders clean empty state."""
    db = TestingSessionLocal()
    dev = Device(
        mac="66:66:66:66:66:66", hostname="fresh-device", status=DeviceStatus.TRUSTED.value
    )
    db.add(dev)
    db.commit()

    client = TestClient(app)
    resp = client.get(f"/devices/{dev.id}", headers=AUTH)
    assert resp.status_code == 200
    assert "No connection transition events recorded yet for this device." in resp.text

    db.close()


def test_timeline_endpoint_404_on_nonexistent_device():
    client = TestClient(app)
    assert client.get("/devices/99999", headers=AUTH).status_code == 404
    assert client.get("/htmx/devices/99999/timeline", headers=AUTH).status_code == 404
