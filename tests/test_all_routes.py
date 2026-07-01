import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime
from unittest.mock import patch

from app.main import app
from app.db import Base, get_db
from app.models import Device, NotificationChannel, Observation

# Use a single test database for everything
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

@pytest.fixture(autouse=True, scope="function")
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    device1 = Device(id=1, mac="aa:bb:cc:dd:ee:ff", ip="192.168.1.100", hostname="test-device", display_name="Old Name", status="unknown")
    device2 = Device(id=2, mac="11:22:33:44:55:66", ip="192.168.1.101", hostname="trusted-device", status="trusted")

    channel1 = NotificationChannel(id=1, type="webhook", name="Test Webhook", enabled=True, config_json='{"url": "http://test.com"}')
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

    Base.metadata.drop_all(bind=engine)

# API Devices
def test_trust_device():
    response = client.post("/api/devices/1/trust")
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().status == "trusted"
    db.close()

def test_ignore_device():
    response = client.post("/api/devices/1/ignore")
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().status == "ignored"
    db.close()

def test_bulk_trust_devices():
    response = client.post("/api/devices/bulk/trust", json={"device_ids": [1]})
    assert response.status_code == 200
    assert response.json()["updated"] == 1
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().status == "trusted"
    db.close()

def test_bulk_ignore_devices():
    response = client.post("/api/devices/bulk/ignore", json={"device_ids": [1, 2]})
    assert response.status_code == 200
    assert response.json()["updated"] == 2
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id.in_([1, 2])).count() == 2
    for d in db.query(Device).filter(Device.id.in_([1, 2])).all():
        assert d.status == "ignored"
    db.close()

def test_reset_device():
    response = client.post("/api/devices/2/reset")
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 2).first().status == "unknown"
    db.close()

def test_delete_device():
    response = client.post("/api/devices/1/delete")
    assert response.status_code == 200
    assert response.json()["mac"] == "aa:bb:cc:dd:ee:ff"
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first() is None
    assert db.query(Observation).filter(Observation.device_id == 1).count() == 0
    db.close()

def test_rename_device():
    response = client.post("/api/devices/1/rename", json={"display_name": "New Name"})
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().display_name == "New Name"
    db.close()

def test_notes_device():
    response = client.post("/api/devices/1/notes", json={"notes": "Some notes"})
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(Device).filter(Device.id == 1).first().notes == "Some notes"
    db.close()

# API Scans
@patch("app.api.routes_scans.run_scan")
def test_run_scan(mock_run_scan):
    response = client.post("/api/scan/run")
    assert response.status_code == 200

# API Notifications
def test_delete_channel():
    response = client.post("/api/notifications/htmx/1/delete")
    assert response.status_code == 200

@patch("app.notifications.webhook.WebhookProvider.send")
def test_test_channel(mock_send):
    mock_send.return_value = (True, 200, "OK", None)
    response = client.post("/api/notifications/htmx/1/test")
    assert response.status_code == 200

# API Import/Export
def test_export_csv():
    response = client.get("/tools/export/csv")
    assert response.status_code == 200
    assert "aa:bb:cc:dd:ee:ff" in response.text

def test_import_trusted():
    file_content = "11:22:33:44:55:66\n22:33:44:55:66:77\n"
    files = {"file": ("trusted.txt", file_content, "text/plain")}
    response = client.post("/tools/htmx/import_trusted", files=files)
    assert response.status_code == 200

# Web Routes
def test_dashboard():
    response = client.get("/")
    assert response.status_code == 200
    assert "Network Overview" in response.text

def test_unknown_devices():
    response = client.get("/unknown")
    assert response.status_code == 200
    assert "aa:bb:cc:dd:ee:ff" in response.text
    assert 'id="bulk-trust-btn"' in response.text
    assert 'id="unknown-devices-tbody"' in response.text
    assert 'device-select' in response.text

def test_inventory():
    response = client.get("/devices")
    assert response.status_code == 200
    assert "11:22:33:44:55:66" in response.text
    assert '/devices/2' in response.text
    assert 'sortable-th' in response.text
    assert 'data-sort-key="mac"' in response.text

def test_nav_hides_unknown_when_none():
    db = TestingSessionLocal()
    for device in db.query(Device).filter(Device.status == "unknown").all():
        device.status = "trusted"
    db.commit()
    db.close()

    response = client.get("/devices")
    assert response.status_code == 200
    assert 'href="/unknown"' not in response.text

def test_device_detail():
    response = client.get("/devices/1")
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

    response = client.get("/api/devices/htmx/1/block_modal")
    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "<img src" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;img" in response.text

def test_notifications():
    response = client.get("/notifications")
    assert response.status_code == 200
    assert '/api/notifications/htmx/1/test' in response.text
    assert '/api/notifications/htmx/1/delete' in response.text
    assert '/api/notifications/htmx/create' in response.text
    assert 'User Key' in response.text
    assert 'HTTP Method' in response.text
    assert 'JSON Request Body' in response.text
    assert 'field-help-tip' in response.text

def test_tools():
    response = client.get("/tools")
    assert response.status_code == 200
    assert '/tools/htmx/import_trusted' in response.text
    assert 'trusted.csv' in response.text
    assert 'mac,display_name' in response.text

def test_logs_page():
    response = client.get("/logs")
    assert response.status_code == 200
    assert "Activity Log" in response.text
    assert '/static/app.css' in response.text
    assert 'cdn.tailwindcss.com' not in response.text
    assert 'href="/logs"' in response.text

def test_htmx_nav():
    response = client.get("/htmx/nav")
    assert response.status_code == 200
    assert 'id="desktop-nav-links"' in response.text
    assert 'id="mobile-nav"' in response.text

def test_htmx_scan_status():
    response = client.get("/htmx/scan-status")
    assert response.status_code == 200
    assert 'id="scan-status-panel"' in response.text
