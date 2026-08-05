from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from app.db import Base, get_db
from app.main import app, readyz
from app.models import Device
from app.security.service import invalidate_security_cache
from app.web.pagination import clamp_pagination, device_status_counts, paginate
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.conftest import basic_auth_header, bootstrap_security_for_tests

SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool
)


@event.listens_for(engine, "connect")
def _fk(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
AUTH = basic_auth_header()


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.main.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.security.middleware.SessionLocal", TestingSessionLocal)
    invalidate_security_cache()
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    bootstrap_security_for_tests(db)
    for i in range(55):
        db.add(
            Device(
                mac=f"aa:bb:cc:dd:ee:{i:02x}",
                status="unknown" if i % 2 == 0 else "trusted",
                last_seen_at=datetime(2025, 1, 1, 0, 0, i % 60),
            )
        )
    db.commit()
    db.close()

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client

    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)
    invalidate_security_cache()
    Base.metadata.drop_all(bind=engine)


def test_device_status_counts_single_aggregate(client):
    db = TestingSessionLocal()
    counts = device_status_counts(db)
    db.close()
    assert counts["total"] == 55
    assert counts["unknown"] + counts["trusted"] + counts["ignored"] == 55
    assert counts["unknown"] == 28
    assert counts["trusted"] == 27


def test_paginate_helper(client):
    db = TestingSessionLocal()
    page, size = clamp_pagination(2, 10)
    result = paginate(db.query(Device).order_by(Device.id), page, size)
    db.close()
    assert result.page == 2
    assert result.page_size == 10
    assert result.total == 55
    assert len(result.items) == 10
    assert result.has_prev is True
    assert result.has_next is True


def test_devices_pagination(client):
    response = client.get("/devices?page=1&page_size=10", headers=AUTH)
    assert response.status_code == 200
    assert "Page 1 of" in response.text
    assert "Next" in response.text


def test_unknown_pagination(client):
    response = client.get("/unknown?page=1&page_size=5", headers=AUTH)
    assert response.status_code == 200
    assert "Page 1 of" in response.text


def test_export_csv_streams_rows(client):
    response = client.get("/tools/export/csv", headers=AUTH)
    assert response.status_code == 200
    assert "aa:bb:cc:dd:ee:00" in response.text
    assert response.text.startswith("ID,MAC,Status")


def test_readyz_ok(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_not_ready_when_db_fails(client):
    with patch("app.main.SessionLocal", side_effect=RuntimeError("db down")):
        response = readyz()
    assert response.status_code == 503
    assert response.body == b'{"status":"not_ready"}'


def test_dashboard_reuses_counts(client):
    response = client.get("/", headers=AUTH)
    assert response.status_code == 200
    assert "Unknown" in response.text
