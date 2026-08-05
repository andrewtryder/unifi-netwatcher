"""Boundary and hardening regression tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Device, Event, NotificationChannel, Observation
from app.scanner import run_scan
from app.security.service import ensure_security_settings, invalidate_security_cache
from app.services.context import RequestContext
from app.services.devices import DeviceNotFoundError, DeviceService
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.conftest import basic_auth_header, bootstrap_security_for_tests

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
    db.add(
        Device(
            mac="aa:bb:cc:dd:ee:01",
            status="unknown",
            hostname="=CMD()",
            display_name="+1-555",
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


def test_protected_routes_require_auth(client):
    for path, method in [
        ("/", "get"),
        ("/devices", "get"),
        ("/api/scan/run", "post"),
        ("/api/devices/1/trust", "post"),
        ("/tools/export/csv", "get"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code == 401, path


def test_cross_origin_mutation_rejected(client):
    response = client.post(
        "/api/devices/1/trust",
        headers={**AUTH, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_rename_validation_no_script(client):
    response = client.post(
        "/api/devices/1/rename",
        headers=AUTH,
        json={"display_name": "<script>alert(1)</script>"},
    )
    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")
    assert response.json()["status"] == "success"

    bad = client.post(
        "/api/devices/1/rename",
        headers=AUTH,
        json={"display_name": ""},
    )
    assert bad.status_code == 422
    assert "<script" not in bad.text.lower()


def test_htmx_rename_escapes_img_onerror(client):
    payload = "<img onerror=alert(1) src=x>"
    response = client.post(
        "/htmx/devices/1/rename",
        headers=AUTH,
        data={"display_name": payload},
    )
    assert response.status_code == 200
    # Must not contain a raw executable tag; autoescape encodes angle brackets.
    assert "<img" not in response.text
    assert "&lt;img" in response.text
    assert "alert(1)" in response.text  # text may remain, but not as HTML attrs on a real tag
    assert not response.text.lower().startswith("<img")


def test_security_headers_present(client):
    response = client.get("/", headers=AUTH)
    assert response.status_code == 200
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers.get("content-security-policy", "")


def test_notification_create_rejects_http_and_escapes(client):
    response = client.post(
        "/api/notifications/htmx/create",
        headers=AUTH,
        data={
            "name": "<script>x</script>",
            "type": "webhook",
            "config_json": '{"url":"http://example.com/hook"}',
        },
    )
    assert response.status_code == 400
    assert "<script>" not in response.text


def test_csv_formula_escaped(client):
    response = client.get("/tools/export/csv", headers=AUTH)
    assert response.status_code == 200
    assert "'=CMD()" in response.text
    assert "'+1-555" in response.text


def test_csv_safe_leading_whitespace():
    from app.api.routes_import_export import _csv_safe

    assert _csv_safe(" =CMD()") == "' =CMD()"
    assert _csv_safe("\t=CMD()") == "'\t=CMD()"
    assert _csv_safe("safe") == "safe"


def test_import_rejects_oversize(client):
    huge = b"aa:bb:cc:dd:ee:ff\n" * 100_000
    response = client.post(
        "/tools/htmx/import_trusted",
        headers=AUTH,
        files={"file": ("big.txt", huge, "text/plain")},
    )
    assert response.status_code == 400
    assert (
        "size limit" in response.text.lower()
        or "1MB" in response.text
        or "limit" in response.text.lower()
    )


def test_import_rejects_bad_utf8(client):
    response = client.post(
        "/tools/htmx/import_trusted",
        headers=AUTH,
        files={"file": ("bad.txt", b"\xff\xfe", "text/plain")},
    )
    assert response.status_code == 400
    assert "UTF-8" in response.text


def test_device_service_not_found_and_actor():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    ensure_security_settings(db)
    db.add(Device(mac="11:22:33:44:55:66", status="unknown"))
    db.commit()
    device = db.query(Device).first()
    svc = DeviceService(
        db,
        RequestContext(actor="admin", client_ip="127.0.0.1", request_id="req-1"),
    )
    updated = svc.trust(device.id)
    assert updated.status == "trusted"
    assert updated.updated_at.tzinfo is not None
    with pytest.raises(DeviceNotFoundError):
        svc.trust(99999)
    db.close()
    Base.metadata.drop_all(bind=engine)


def test_malformed_channel_does_not_roll_back_observations(monkeypatch):
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    ensure_security_settings(db)
    monkeypatch.setattr(settings, "UNIFI_MOCK_MODE", True)
    monkeypatch.setattr(settings, "ALERT_COOLDOWN_SECONDS", 0)

    db.add(
        NotificationChannel(
            name="bad",
            type="webhook",
            enabled=True,
            config_json="not-json",
        )
    )
    good_calls: list[str] = []

    class GoodProvider:
        def validate_config(self, config):
            return True

        def send(self, message, config):
            good_calls.append(message)
            return True, 200, "ok", ""

    db.add(
        NotificationChannel(
            name="good",
            type="pushover",
            enabled=True,
            config_json={"token": "12345678", "user": "87654321"},
        )
    )
    db.commit()

    monkeypatch.setattr(
        "app.scanner.PROVIDERS",
        {
            "webhook": MagicMock(
                validate_config=MagicMock(side_effect=ValueError("bad")),
                send=MagicMock(),
            ),
            "pushover": GoodProvider(),
        },
    )

    result = run_scan(db)
    assert result["success"] is True
    assert db.query(Observation).count() > 0
    assert good_calls
    assert db.query(Event).filter(Event.event_type == "scan_finished").count() == 1
    db.close()
    Base.metadata.drop_all(bind=engine)


def test_cooldown_logic_settings():
    """Cooldown seconds are configured; scanner tests cover send suppression."""
    assert isinstance(settings.ALERT_COOLDOWN_SECONDS, int)
    assert settings.ALERT_COOLDOWN_SECONDS >= 0
