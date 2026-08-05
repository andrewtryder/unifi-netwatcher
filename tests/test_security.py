"""Tests for application-level HTTP Basic auth and CIDR access control."""

from __future__ import annotations

import pytest
from app.db import Base, get_db
from app.main import app
from app.security.models import SecuritySettings
from app.security.service import (
    authenticate,
    ensure_security_settings,
    get_allowed_cidrs_list,
    get_allowed_hosts_list,
    get_security_settings,
    invalidate_security_cache,
    parse_and_normalize_cidrs,
    update_authentication,
    verify_password,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.conftest import basic_auth_header, bootstrap_security_for_tests

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


@pytest.fixture()
def sec_client(monkeypatch):
    monkeypatch.setattr("app.db.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.main.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.security.middleware.SessionLocal", TestingSessionLocal)
    invalidate_security_cache()
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    bootstrap_security_for_tests(db)
    db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    invalidate_security_cache()
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)


AUTH = basic_auth_header()


def test_init_defaults(sec_client):
    db = TestingSessionLocal()
    settings = get_security_settings(db)
    assert settings.authentication_enabled is True
    assert settings.authentication_username == "admin"
    assert settings.default_credentials_active is True
    assert settings.cidr_restriction_enabled is False
    # Fixture disables host restriction for TestClient; column default is True.
    assert settings.host_restriction_enabled is False
    assert get_allowed_cidrs_list(settings) == []
    assert "admin" not in settings.authentication_password_hash
    assert verify_password("admin", settings.authentication_password_hash)
    ensure_security_settings(db)
    assert db.query(SecuritySettings).count() == 1
    db.close()


def test_host_restriction_defaults_on_fresh_row():
    from app.security.service import host_allowed, parse_and_normalize_hosts

    assert host_allowed(
        "192.168.1.50",
        host_restriction_enabled=True,
        allowed_hosts=[],
    )
    assert host_allowed(
        "localhost",
        host_restriction_enabled=True,
        allowed_hosts=[],
    )
    assert not host_allowed(
        "evil.example",
        host_restriction_enabled=True,
        allowed_hosts=[],
    )
    assert host_allowed(
        "netwatcher.home.arpa",
        host_restriction_enabled=True,
        allowed_hosts=["netwatcher.home.arpa"],
    )
    norms, errs = parse_and_normalize_hosts(
        "NetWatcher.Home.Arpa\n\nnetwatcher.home.arpa\nbad/name"
    )
    assert norms == ["netwatcher.home.arpa"]
    assert errs


def test_trusted_host_enforcement(sec_client):
    db = TestingSessionLocal()
    settings = get_security_settings(db)
    settings.host_restriction_enabled = True
    settings.allowed_hosts = "[]"
    db.add(settings)
    db.commit()
    db.close()
    invalidate_security_cache()

    # Private IP Host is allowed by default.
    assert sec_client.get("/", headers={**AUTH, "Host": "192.168.1.10:8080"}).status_code == 200
    # Arbitrary DNS Host is rejected.
    assert sec_client.get("/", headers={**AUTH, "Host": "evil.example"}).status_code == 403

    # Allowlist + save via API
    r = sec_client.post(
        "/security/htmx/hosts",
        data={
            "host_restriction_enabled": "on",
            "allowed_hosts": "netwatcher.home.arpa",
        },
        headers={**AUTH, "Host": "192.168.1.10"},
    )
    assert r.status_code == 200
    assert sec_client.get("/", headers={**AUTH, "Host": "netwatcher.home.arpa"}).status_code == 200


def test_trusted_host_lockout_preview(sec_client):
    r = sec_client.post(
        "/security/api/hosts-preview",
        data={
            "host_restriction_enabled": "on",
            "allowed_hosts": "other.example",
        },
        headers={**AUTH, "Host": "testserver"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["client_will_remain_allowed"] is False
    assert body["effective_request_host"] == "testserver"

    r = sec_client.post(
        "/security/htmx/hosts",
        data={
            "host_restriction_enabled": "on",
            "allowed_hosts": "other.example",
        },
        headers={**AUTH, "Host": "testserver"},
    )
    assert r.status_code == 400

    r = sec_client.post(
        "/security/htmx/hosts",
        data={
            "host_restriction_enabled": "on",
            "allowed_hosts": "other.example",
            "allow_lockout": "on",
            "lockout_confirmation": "ALLOW LOCKOUT",
        },
        headers={**AUTH, "Host": "testserver"},
    )
    assert r.status_code == 200


def test_username_colon_rejected(sec_client):
    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "authentication_enabled": "on",
            "username": "admin:evil",
            "current_password": "admin",
            "new_password": "",
            "confirm_password": "",
        },
        headers=AUTH,
    )
    assert r.status_code == 400
    assert "colon" in r.text.lower() or "ascii" in r.text.lower()


def test_auth_rate_limit(sec_client):
    from app.security.middleware import AUTH_FAIL_LIMIT, reset_auth_failure_limiter

    reset_auth_failure_limiter()
    for _ in range(AUTH_FAIL_LIMIT):
        r = sec_client.get("/", headers=basic_auth_header("admin", "wrong-password"))
        assert r.status_code == 401
    r = sec_client.get("/", headers=basic_auth_header("admin", "wrong-password"))
    assert r.status_code == 429
    # Valid credentials still blocked until window clears / limiter reset
    assert sec_client.get("/", headers=AUTH).status_code == 429
    reset_auth_failure_limiter()
    assert sec_client.get("/", headers=AUTH).status_code == 200


def test_admin_admin_authenticates(sec_client):
    r = sec_client.get("/", headers=AUTH)
    assert r.status_code == 200


def test_wrong_username_fails(sec_client):
    r = sec_client.get("/", headers=basic_auth_header("nope", "admin"))
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").startswith("Basic")


def test_wrong_password_fails(sec_client):
    r = sec_client.get("/", headers=basic_auth_header("admin", "wrong"))
    assert r.status_code == 401


def test_protected_without_credentials(sec_client):
    assert sec_client.get("/").status_code == 401
    assert sec_client.post("/api/scan/run").status_code == 401


def test_health_endpoints_public(sec_client):
    assert sec_client.get("/healthz").status_code == 200
    assert sec_client.get("/readyz").status_code == 200


def test_password_change_rules(sec_client):
    # too short
    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "authentication_enabled": "on",
            "username": "admin",
            "current_password": "admin",
            "new_password": "shortpass",
            "confirm_password": "shortpass",
        },
        headers=AUTH,
    )
    assert r.status_code == 400
    assert "12" in r.text

    # mismatch
    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "authentication_enabled": "on",
            "username": "admin",
            "current_password": "admin",
            "new_password": "long-enough-password",
            "confirm_password": "different-password",
        },
        headers=AUTH,
    )
    assert r.status_code == 400

    # success
    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "authentication_enabled": "on",
            "username": "admin",
            "current_password": "admin",
            "new_password": "long-enough-password",
            "confirm_password": "long-enough-password",
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    assert "argon2" not in r.text.lower()
    assert "long-enough-password" not in r.text

    assert sec_client.get("/", headers=AUTH).status_code == 401
    new_auth = basic_auth_header("admin", "long-enough-password")
    page = sec_client.get("/", headers=new_auth)
    assert page.status_code == 200
    assert "default admin credentials" not in page.text.lower()


def test_username_change_requires_current_password(sec_client):
    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "authentication_enabled": "on",
            "username": "ops",
            "current_password": "",
            "new_password": "",
            "confirm_password": "",
        },
        headers=AUTH,
    )
    assert r.status_code == 400

    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "authentication_enabled": "on",
            "username": "ops",
            "current_password": "admin",
            "new_password": "",
            "confirm_password": "",
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    assert sec_client.get("/", headers=AUTH).status_code == 401
    assert sec_client.get("/", headers=basic_auth_header("ops", "admin")).status_code == 200


def test_disable_auth_requires_password_and_confirm(sec_client):
    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "username": "admin",
            "current_password": "admin",
            "new_password": "",
            "confirm_password": "",
        },
        headers=AUTH,
    )
    assert r.status_code == 400

    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "username": "admin",
            "current_password": "admin",
            "confirm_disable": "on",
        },
        headers=AUTH,
    )
    assert r.status_code == 200

    # general routes open
    assert sec_client.get("/").status_code == 200
    # security still protected
    assert sec_client.get("/security").status_code == 401
    assert sec_client.get("/security", headers=AUTH).status_code == 200

    # re-enable
    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "authentication_enabled": "on",
            "username": "admin",
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    assert sec_client.get("/").status_code == 401
    assert sec_client.get("/", headers=AUTH).status_code == 200


def test_cidr_parse_normalize_and_limits():
    norms, errs = parse_and_normalize_cidrs(
        "192.168.1.10/24\n\n10.0.0.25/32\n192.168.1.0/24\nnot-a-cidr\nfd00:1234::/64"
    )
    assert "192.168.1.0/24" in norms
    assert "10.0.0.25/32" in norms
    assert "fd00:1234::/64" in norms
    assert norms.count("192.168.1.0/24") == 1
    assert any("invalid" in e.lower() for e in errs)

    lines = "\n".join(f"10.0.{i}.0/32" for i in range(101))
    _, errs = parse_and_normalize_cidrs(lines)
    assert any("100" in e for e in errs)


def test_cidr_preview_and_xff_ignored(sec_client, monkeypatch):
    monkeypatch.setattr(
        "app.security.routes.effective_client_ip",
        lambda request: "192.168.1.25",
    )
    r = sec_client.post(
        "/security/api/cidr-preview",
        data={
            "cidr_restriction_enabled": "on",
            "allowed_cidrs": "192.168.1.0/24",
        },
        headers={**AUTH, "X-Forwarded-For": "8.8.8.8"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["effective_client_ip"] == "192.168.1.25"
    assert body["client_will_remain_allowed"] is True
    assert body["normalized_cidrs"] == ["192.168.1.0/24"]


def test_cidr_enforcement(sec_client, monkeypatch):
    monkeypatch.setattr(
        "app.security.middleware.effective_client_ip",
        lambda request: "192.168.1.25",
    )
    monkeypatch.setattr(
        "app.security.routes.effective_client_ip",
        lambda request: "192.168.1.25",
    )
    r = sec_client.post(
        "/security/htmx/cidr",
        data={
            "cidr_restriction_enabled": "on",
            "allowed_cidrs": "192.168.1.0/24",
        },
        headers=AUTH,
    )
    assert r.status_code == 200

    assert sec_client.get("/", headers=AUTH).status_code == 200
    assert sec_client.get("/healthz").status_code == 200

    monkeypatch.setattr(
        "app.security.middleware.effective_client_ip",
        lambda request: "10.9.9.9",
    )
    assert sec_client.get("/", headers=AUTH).status_code == 403


def test_cidr_empty_enable_rejected(sec_client):
    r = sec_client.post(
        "/security/htmx/cidr",
        data={"cidr_restriction_enabled": "on", "allowed_cidrs": ""},
        headers=AUTH,
    )
    assert r.status_code == 400


def test_cidr_lockout_requires_phrase(sec_client, monkeypatch):
    monkeypatch.setattr(
        "app.security.routes.effective_client_ip",
        lambda request: "192.168.1.25",
    )
    r = sec_client.post(
        "/security/htmx/cidr",
        data={
            "cidr_restriction_enabled": "on",
            "allowed_cidrs": "10.0.0.0/8",
        },
        headers=AUTH,
    )
    assert r.status_code == 400

    r = sec_client.post(
        "/security/htmx/cidr",
        data={
            "cidr_restriction_enabled": "on",
            "allowed_cidrs": "10.0.0.0/8",
            "allow_lockout": "on",
            "lockout_confirmation": "ALLOW LOCKOUT",
        },
        headers=AUTH,
    )
    assert r.status_code == 200


def test_same_origin_mismatch(sec_client):
    r = sec_client.post(
        "/security/htmx/authentication",
        data={
            "authentication_enabled": "on",
            "username": "admin",
            "current_password": "admin",
            "new_password": "long-enough-password",
            "confirm_password": "long-enough-password",
        },
        headers={**AUTH, "Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_security_page_escapes_username_in_form(sec_client):
    db = TestingSessionLocal()
    update_authentication(
        db,
        authentication_enabled=True,
        username="admin_ops",
        current_password="admin",
        new_password="",
        confirm_password="",
        confirm_disable=False,
    )
    db.close()
    invalidate_security_cache()

    evil_auth = basic_auth_header("admin_ops", "admin")
    r = sec_client.get("/security", headers=evil_auth)
    assert r.status_code == 200
    assert 'value="admin_ops"' in r.text


def test_authenticate_helper_constant_time_path(sec_client):
    db = TestingSessionLocal()
    settings = get_security_settings(db)
    assert authenticate(settings, "admin", "admin")
    assert not authenticate(settings, "admin", "nope")
    assert not authenticate(settings, "nope", "admin")
    db.close()


def test_fresh_security_row_enables_host_restriction():
    """create_all + ensure without test bootstrap keeps host restriction on."""
    from app.db import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Session = sessionmaker(bind=eng)
    Base.metadata.create_all(bind=eng)
    db = Session()
    invalidate_security_cache()
    row = ensure_security_settings(db)
    assert row.host_restriction_enabled is True
    assert get_allowed_hosts_list(row) == []
    db.close()
    Base.metadata.drop_all(bind=eng)
