from unittest.mock import MagicMock

import pytest
from app.config import settings
from app.db import Base
from app.models import (
    Device,
    Event,
    NotificationChannel,
    NotificationDelivery,
    Observation,
    OuiEntry,
)
from app.scanner import SCAN_BUSY_MESSAGE, _scan_lock, run_scan
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Use an in-memory SQLite database for testing
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def test_run_scan_with_mock_data(db_session, monkeypatch):
    # Ensure we use mock data
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)

    run_scan(db_session)

    devices = db_session.query(Device).all()
    assert len(devices) == 4

    macs = [d.mac for d in devices]
    assert "aa:bb:cc:dd:ee:ff" in macs
    assert "bc:34:11:9d:a3:c1" in macs
    assert "de:ad:be:ef:00:01" in macs
    assert "f0:18:98:12:34:56" in macs

    wifi = next(d for d in devices if d.mac == "aa:bb:cc:dd:ee:ff")
    assert wifi.is_wired is False
    assert wifi.satisfaction == 88
    assert wifi.rssi == -62
    assert wifi.rx_bytes_r == 450000
    assert wifi.name == "Guest Phone"

    wired = next(d for d in devices if d.mac == "bc:34:11:9d:a3:c1")
    assert wired.is_wired is True
    assert wired.sw_port == 8

    observations = db_session.query(Observation).all()
    assert len(observations) == 4

    # Check events
    events = db_session.query(Event).filter(Event.event_type == "discovered").all()
    assert len(events) == 4

    # Run scan again
    run_scan(db_session)

    # Should not create new devices or discovered events
    devices = db_session.query(Device).all()
    assert len(devices) == 4

    events = db_session.query(Event).filter(Event.event_type == "discovered").all()
    assert len(events) == 4

    # But observations should double
    observations = db_session.query(Observation).all()
    assert len(observations) == 8


def test_run_scan_uses_batch_oui_vendor(db_session, monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)
    db_session.add(OuiEntry(mac_prefix="aa:bb:cc", vendor="BatchCorp"))
    db_session.commit()

    run_scan(db_session)

    wifi = db_session.query(Device).filter(Device.mac == "aa:bb:cc:dd:ee:ff").one()
    assert wifi.vendor == "BatchCorp"


def test_run_scan_busy_when_lock_held(db_session, monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)
    assert _scan_lock.acquire(blocking=False)
    try:
        result = run_scan(db_session, source="manual")
        assert result["success"] is False
        assert result["busy"] is True
        assert result["message"] == SCAN_BUSY_MESSAGE
        assert result["devices_processed"] == 0
        assert db_session.query(Device).count() == 0
    finally:
        _scan_lock.release()


def test_alerts_sent_after_scan_commit(db_session, monkeypatch):
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)
    monkeypatch.setattr(settings, "ALERT_COOLDOWN_SECONDS", 3600)

    db_session.add(
        NotificationChannel(
            name="hook",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/hook"},
        )
    )
    db_session.commit()

    send_calls = []

    def mock_send(message, config):
        send_calls.append((message, config))
        # Scan mutations should already be committed when providers run
        assert db_session.query(Event).filter(Event.event_type == "scan_finished").count() == 1
        return True, 200, "ok", ""

    provider = MagicMock()
    provider.validate_config.return_value = True
    provider.send.side_effect = mock_send
    monkeypatch.setattr("app.scanner.PROVIDERS", {"webhook": provider})

    result = run_scan(db_session)
    assert result["success"] is True
    assert len(send_calls) == 4  # four unknown devices

    queued = db_session.query(Event).filter(Event.event_type == "alert_queued").count()
    sent = db_session.query(Event).filter(Event.event_type == "alert_sent").count()
    deliveries = db_session.query(NotificationDelivery).count()
    assert queued == 4
    assert sent == 4
    assert deliveries == 4

    # Cooldown: second scan should not re-alert
    send_calls.clear()
    run_scan(db_session)
    assert send_calls == []
