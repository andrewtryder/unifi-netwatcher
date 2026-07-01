import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime
from unittest.mock import patch

from app.main import app
from app.db import Base, get_db
from app.models import Device, NotificationChannel

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

    db.add(device1)
    db.add(device2)
    db.add(channel1)
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

def test_inventory():
    response = client.get("/devices")
    assert response.status_code == 200
    assert "11:22:33:44:55:66" in response.text

def test_device_detail():
    response = client.get("/devices/1")
    assert response.status_code == 200

def test_notifications():
    response = client.get("/notifications")
    assert response.status_code == 200

def test_tools():
    response = client.get("/tools/tools")
    assert response.status_code == 200
