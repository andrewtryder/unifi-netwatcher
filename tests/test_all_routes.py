from datetime import datetime
from unittest.mock import patch

import pytest
from app.db import Base, get_db
from app.main import app
from app.models import Device, NotificationChannel, Observation
from app.security.service import ensure_security_settings, invalidate_security_cache
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.conftest import basic_auth_header

# Use a single test database for everything
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)
AUTH = basic_auth_header()


@pytest.fixture(autouse=True, scope="function")
def setup_db(monkeypatch):
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.security.middleware.SessionLocal", TestingSessionLocal)
    invalidate_security_cache()

    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    ensure_security_settings(db)

    device1 = Device(
        id=1,
        mac="aa:bb:cc:dd:ee:ff",
        ip="192.168.1.100",
        hostname="test-device",
        display_name="Old Name",
        status="unknown",
    )
    device2 = Device(
        id=2,
        mac="11:22:33:44:55:66",
        ip="192.168.1.101",
        hostname="trusted-device",
        status="trusted",
    )

    channel1 = NotificationChannel(
        id=1,
        type="webhook",
        name="Test Webhook",
        enabled=True,
        config_json='{"url": "http://test.com"}',
    )
    observation1 = Observation(
        device_id=1,
        mac="aa:bb:cc:dd:ee:ff",
        ip="192.168.1.100",
        hostname="test-device",
        seen_at=datetime(2025, 1, 15, 12, 0),
    )

    db.add(device1)
    db.add(device2)
    db.add(channel1)
    db.add(observation1)
    db.commit()
    db.close()

    yield

    invalidate_security_cache()
    Base.metadata.drop_all(bind=engine)


# API Devices
def test_trust_device():
    response = client.post("/api/devices/1/trust", headers=AUTH)
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().status == "trusted"
    db.close()


def test_ignore_device():
    response = client.post("/api/devices/1/ignore", headers=AUTH)
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().status == "ignored"
    db.close()


def test_bulk_trust_devices():
    response = client.post("/api/devices/bulk/trust", json={"device_ids": [1]}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["updated"] == 1
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().status == "trusted"
    db.close()


def test_bulk_ignore_devices():
    response = client.post("/api/devices/bulk/ignore", json={"device_ids": [1, 2]}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["updated"] == 2
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id.in_([1, 2])).count() == 2
    for d in db.query(Device).filter(Device.id.in_([1, 2])).all():
        assert d.status == "ignored"
    db.close()


def test_reset_device():
    response = client.post("/api/devices/2/reset", headers=AUTH)
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 2).first().status == "unknown"
    db.close()


def test_delete_device():
    response = client.post("/api/devices/1/delete", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["mac"] == "aa:bb:cc:dd:ee:ff"
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first() is None
    assert db.query(Observation).filter(Observation.device_id == 1).count() == 0
    db.close()


def test_rename_device():
    response = client.post("/api/devices/1/rename", json={"display_name": "New Name"}, headers=AUTH)
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().display_name == "New Name"
    db.close()


def test_notes_device():
    response = client.post("/api/devices/1/notes", json={"notes": "Some notes"}, headers=AUTH)
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().notes == "Some notes"
    db.close()


# API Scans
@patch("app.api.routes_scans.run_scan")
def test_run_scan(mock_run_scan):
    mock_run_scan.return_value = {
        "success": True,
        "message": "ok",
        "devices_processed": 0,
    }
    response = client.post("/api/scan/run", headers=AUTH)
    assert response.status_code == 200


@patch("app.api.routes_scans.run_scan")
def test_run_scan_busy_returns_409(mock_run_scan):
    from app.scanner import SCAN_BUSY_MESSAGE

    mock_run_scan.return_value = {
        "success": False,
        "message": SCAN_BUSY_MESSAGE,
        "devices_processed": 0,
        "busy": True,
    }
    response = client.post("/api/scan/run", headers=AUTH)
    assert response.status_code == 409
    assert SCAN_BUSY_MESSAGE in response.json()["detail"]


# API Notifications
def test_delete_channel():
    response = client.post("/api/notifications/htmx/1/delete", headers=AUTH)
    assert response.status_code == 200


@patch("app.notifications.webhook.WebhookProvider.send")
def test_test_channel(mock_send):
    mock_send.return_value = (True, 200, "OK", None)
    response = client.post("/api/notifications/htmx/1/test", headers=AUTH)
    assert response.status_code == 200


# API Import/Export
def test_export_csv():
    response = client.get("/tools/export/csv", headers=AUTH)
    assert response.status_code == 200
    assert "aa:bb:cc:dd:ee:ff" in response.text


def test_import_trusted():
    file_content = "11:22:33:44:55:66\n22:33:44:55:66:77\n"
    files = {"file": ("trusted.txt", file_content, "text/plain")}
    response = client.post("/tools/htmx/import_trusted", files=files, headers=AUTH)
    assert response.status_code == 200


# Web Routes
def test_dashboard():
    response = client.get("/", headers=AUTH)
    assert response.status_code == 200
    assert "Network Overview" in response.text
    assert "Network Health" in response.text
    assert "Live Throughput" in response.text
    assert "Needs Attention" in response.text
    assert "Recent Activity" in response.text


def test_unknown_devices():
    response = client.get("/unknown", headers=AUTH)
    assert response.status_code == 200
    assert "aa:bb:cc:dd:ee:ff" in response.text
    assert 'id="bulk-trust-btn"' in response.text
    assert 'id="unknown-devices-tbody"' in response.text
    assert "device-select" in response.text


def test_inventory():
    response = client.get("/devices", headers=AUTH)
    assert response.status_code == 200
    assert "11:22:33:44:55:66" in response.text
    assert "/devices/2" in response.text
    assert "sortable-th" in response.text
    assert 'data-sort-key="name"' in response.text
    assert 'id="inventory-tbody"' in response.text
    assert "inventory-row" in response.text


def test_nav_hides_unknown_when_none():
    db = TestingSessionLocal()
    for device in db.query(Device).filter(Device.status == "unknown").all():
        device.status = "trusted"
    db.commit()
    db.close()

    response = client.get("/devices", headers=AUTH)
    assert response.status_code == 200
    assert 'href="/unknown"' not in response.text


def test_device_detail():
    response = client.get("/devices/1", headers=AUTH)
    assert response.status_code == 200
    assert 'data-device-action="trust"' in response.text
    assert 'data-device-action="ignore"' in response.text
    assert 'data-device-action="delete"' in response.text
    assert "2025-01-15 12:00" in response.text
    assert 'hx-target="#device-actions"' not in response.text


def test_block_modal_escapes_device_fields():
    db = TestingSessionLocal()
    device = db.query(Device).filter(Device.id == 1).first()
    device.display_name = None
    device.hostname = '<script>alert("xss")</script>'
    device.ip = '"><img src=x onerror=alert(1)>'
    db.commit()
    db.close()

    response = client.get("/api/devices/htmx/1/block_modal", headers=AUTH)
    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "<img src" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;img" in response.text


def test_notifications():
    response = client.get("/notifications", headers=AUTH)
    assert response.status_code == 200
    assert "/api/notifications/htmx/1/test" in response.text
    assert "/api/notifications/htmx/1/delete" in response.text
    assert "/api/notifications/htmx/create" in response.text
    assert "User Key" in response.text
    assert "HTTP Method" in response.text
    assert "JSON Request Body" in response.text
    assert "field-help-tip" in response.text


def test_tools():
    response = client.get("/tools", headers=AUTH)
    assert response.status_code == 200
    assert "/tools/htmx/import_trusted" in response.text
    assert "trusted.csv" in response.text
    assert "mac,display_name" in response.text
    assert "Scan Schedule" in response.text
    assert "/tools/htmx/scan-interval" in response.text
    assert "Data Retention" in response.text
    assert "/tools/htmx/retention" in response.text
    assert "environment default" in response.text


def test_save_scan_interval():
    from app.models import Setting
    from app.web.display import SCAN_INTERVAL_SETTING_KEY

    response = client.post(
        "/tools/htmx/scan-interval",
        data={"interval_mode": "900"},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert "900" in response.text
    assert "stored override" in response.text

    db = TestingSessionLocal()
    row = db.query(Setting).filter(Setting.key == SCAN_INTERVAL_SETTING_KEY).first()
    assert row is not None
    assert row.value == "900"
    db.close()

    page = client.get("/tools", headers=AUTH)
    assert page.status_code == 200
    assert "stored override" in page.text


def test_save_retention():
    from app.models import Setting
    from app.web.display import (
        EVENT_RETENTION_SETTING_KEY,
        OBSERVATION_RETENTION_SETTING_KEY,
    )

    response = client.post(
        "/tools/htmx/retention",
        data={
            "observation_mode": "7",
            "event_mode": "custom",
            "event_custom_days": "120",
        },
        headers=AUTH,
    )
    assert response.status_code == 200
    assert "7 days" in response.text
    assert "120 days" in response.text
    assert "stored override" in response.text

    db = TestingSessionLocal()
    obs = db.query(Setting).filter(Setting.key == OBSERVATION_RETENTION_SETTING_KEY).first()
    events = db.query(Setting).filter(Setting.key == EVENT_RETENTION_SETTING_KEY).first()
    assert obs is not None and obs.value == "7"
    assert events is not None and events.value == "120"
    db.close()

    page = client.get("/tools", headers=AUTH)
    assert page.status_code == 200
    assert "stored override" in page.text


def test_logs_page():
    response = client.get("/logs", headers=AUTH)
    assert response.status_code == 200
    assert "Activity Log" in response.text
    assert "/static/app.css" in response.text
    assert "cdn.tailwindcss.com" not in response.text
    assert 'href="/logs"' in response.text


def test_htmx_nav():
    response = client.get("/htmx/nav", headers=AUTH)
    assert response.status_code == 200
    assert 'id="desktop-nav-links"' in response.text
    assert 'id="mobile-nav"' in response.text


def test_htmx_scan_status():
    response = client.get("/htmx/scan-status", headers=AUTH)
    assert response.status_code == 200
    assert 'id="scan-status-panel"' in response.text
