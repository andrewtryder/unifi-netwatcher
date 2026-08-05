"""Shared test fixtures for auth-aware TestClient usage."""

from __future__ import annotations

import base64

import pytest
from app.security.service import invalidate_security_cache


def basic_auth_header(username: str = "admin", password: str = "admin") -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def _clear_security_cache():
    invalidate_security_cache()
    yield
    invalidate_security_cache()
