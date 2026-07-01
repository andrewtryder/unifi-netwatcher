import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Device, Observation, Event
from app.scanner import run_scan
from app.config import settings

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
    assert len(devices) == 2
    
    macs = [d.mac for d in devices]
    assert "aa:bb:cc:dd:ee:ff" in macs
    assert "bc:34:11:9d:a3:c1" in macs
    
    observations = db_session.query(Observation).all()
    assert len(observations) == 2
    
    # Check events
    events = db_session.query(Event).filter(Event.event_type == "discovered").all()
    assert len(events) == 2

    # Run scan again
    run_scan(db_session)
    
    # Should not create new devices or discovered events
    devices = db_session.query(Device).all()
    assert len(devices) == 2
    
    events = db_session.query(Event).filter(Event.event_type == "discovered").all()
    assert len(events) == 2
    
    # But observations should double
    observations = db_session.query(Observation).all()
    assert len(observations) == 4
