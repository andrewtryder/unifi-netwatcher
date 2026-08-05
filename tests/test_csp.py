"""CSP nonce and dangerous markup regressions on main HTML pages."""

from __future__ import annotations

import re

import pytest
from app.db import Base, get_db
from app.main import app
from app.models import Device
from app.security.service import invalidate_security_cache
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.conftest import basic_auth_header, bootstrap_security_for_tests

AUTH = basic_auth_header()
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.main.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.security.middleware.SessionLocal", TestingSessionLocal)
    invalidate_security_cache()
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    bootstrap_security_for_tests(db)
    db.add(Device(mac="aa:bb:cc:dd:ee:01", status="unknown"))
    db.commit()
    db.close()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)
    invalidate_security_cache()
    Base.metadata.drop_all(bind=engine)


PAGES = ["/", "/devices", "/unknown", "/tools", "/security", "/notifications"]


def test_main_pages_script_nonce_and_no_handlers(client):
    for path in PAGES:
        response = client.get(path, headers=AUTH)
        assert response.status_code == 200, path
        csp = response.headers.get("content-security-policy", "")
        match = re.search(r"'nonce-([^']+)'", csp)
        assert match, f"CSP nonce missing for {path}"
        nonce = match.group(1)
        html = response.text
        assert "onclick=" not in html.lower()
        assert "javascript:" not in html.lower()
        for script in re.findall(r"<script\b[^>]*>", html, flags=re.I):
            assert f'nonce="{nonce}"' in script or f"nonce='{nonce}'" in script, (
                f"script without nonce on {path}: {script}"
            )
