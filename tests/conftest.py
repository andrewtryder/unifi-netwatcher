"""Shared test fixtures for auth-aware TestClient usage."""

from __future__ import annotations

import base64
import os

# Must run before app.config is imported by other test modules.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_SECRET_KEY", "pytest-secret-key-not-for-prod")

import pytest
from app.security.middleware import reset_auth_failure_limiter
from app.security.secrets import init_app_secrets, reset_secrets_for_tests
from app.security.service import ensure_security_settings, invalidate_security_cache
from sqlalchemy.orm import Session


def basic_auth_header(username: str = "admin", password: str = "admin") -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def bootstrap_security_for_tests(db: Session) -> None:
    """Create security settings and disable host restriction for TestClient.

    Starlette's TestClient sends ``Host: testserver``, which is neither a
    private IP literal nor localhost — trusted-host tests re-enable restriction.
    """
    reset_secrets_for_tests()
    init_app_secrets(env_secret=os.environ.get("APP_SECRET_KEY", "pytest-secret-key-not-for-prod"))
    row = ensure_security_settings(db)
    row.host_restriction_enabled = False
    db.add(row)
    db.commit()
    db.refresh(row)
    invalidate_security_cache()
    ensure_security_settings(db)


@pytest.fixture(autouse=True)
def _clear_security_cache():
    reset_secrets_for_tests()
    init_app_secrets(env_secret=os.environ.get("APP_SECRET_KEY", "pytest-secret-key-not-for-prod"))
    invalidate_security_cache()
    reset_auth_failure_limiter()
    yield
    invalidate_security_cache()
    reset_auth_failure_limiter()
    reset_secrets_for_tests()
