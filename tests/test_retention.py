from datetime import datetime, timedelta

import pytest
from app.db import Base
from app.models import (
    Device,
    Event,
    NotificationChannel,
    NotificationDelivery,
    Observation,
    Setting,
)
from app.retention import run_retention
from app.web.display import (
    EVENT_RETENTION_SETTING_KEY,
    OBSERVATION_RETENTION_SETTING_KEY,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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


def _seed(db, *, now: datetime):
    device = Device(
        mac="aa:bb:cc:dd:ee:01",
        status="unknown",
        first_seen_at=now - timedelta(days=100),
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(device)
    db.flush()

    channel = NotificationChannel(
        type="webhook",
        name="hook",
        enabled=True,
        config_json='{"url":"http://example.test"}',
    )
    db.add(channel)
    db.flush()

    old_obs = Observation(
        device_id=device.id,
        mac=device.mac,
        seen_at=now - timedelta(days=40),
    )
    young_obs = Observation(
        device_id=device.id,
        mac=device.mac,
        seen_at=now - timedelta(days=5),
    )
    db.add_all([old_obs, young_obs])

    old_event = Event(
        device_id=device.id,
        event_type="discovered",
        message="old",
        created_at=now - timedelta(days=100),
    )
    young_event = Event(
        device_id=device.id,
        event_type="discovered",
        message="young",
        created_at=now - timedelta(days=10),
    )
    db.add_all([old_event, young_event])
    db.flush()

    db.add(
        NotificationDelivery(
            event_id=old_event.id,
            channel_id=channel.id,
            success=True,
            status_code=200,
            created_at=now - timedelta(days=100),
        )
    )
    db.add(
        NotificationDelivery(
            event_id=young_event.id,
            channel_id=channel.id,
            success=True,
            status_code=200,
            created_at=now - timedelta(days=10),
        )
    )
    db.commit()
    return device


def test_run_retention_deletes_old_rows(db_session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "OBSERVATION_RETENTION_DAYS", 30)
    monkeypatch.setattr(settings, "EVENT_RETENTION_DAYS", 90)

    now = datetime(2026, 8, 4, 12, 0, 0)
    _seed(db_session, now=now)

    result = run_retention(db_session, now=now)
    assert result["observations_deleted"] == 1
    assert result["events_deleted"] == 1
    assert result["deliveries_deleted"] == 1

    assert db_session.query(Observation).count() == 1
    assert db_session.query(Observation).one().seen_at == now - timedelta(days=5)
    assert db_session.query(Event).filter(Event.event_type == "discovered").count() == 1
    assert db_session.query(NotificationDelivery).count() == 1
    assert db_session.query(Event).filter(Event.event_type == "retention_run").count() == 1


def test_run_retention_zero_disables(db_session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "OBSERVATION_RETENTION_DAYS", 0)
    monkeypatch.setattr(settings, "EVENT_RETENTION_DAYS", 0)

    now = datetime(2026, 8, 4, 12, 0, 0)
    _seed(db_session, now=now)

    result = run_retention(db_session, now=now)
    assert result["observations_deleted"] == 0
    assert result["events_deleted"] == 0
    assert result["deliveries_deleted"] == 0
    assert db_session.query(Observation).count() == 2
    assert db_session.query(Event).filter(Event.event_type == "discovered").count() == 2


def test_run_retention_uses_stored_override(db_session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "OBSERVATION_RETENTION_DAYS", 365)
    monkeypatch.setattr(settings, "EVENT_RETENTION_DAYS", 365)

    now = datetime(2026, 8, 4, 12, 0, 0)
    _seed(db_session, now=now)
    db_session.add(Setting(key=OBSERVATION_RETENTION_SETTING_KEY, value="30"))
    db_session.add(Setting(key=EVENT_RETENTION_SETTING_KEY, value="90"))
    db_session.commit()

    result = run_retention(db_session, now=now)
    assert result["observation_retention_days"] == 30
    assert result["event_retention_days"] == 90
    assert result["observations_deleted"] == 1
    assert result["events_deleted"] == 1
