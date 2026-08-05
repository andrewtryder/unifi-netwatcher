"""Centralized HTTP Basic auth, CIDR filtering, trusted hosts, and same-origin checks."""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
import uuid
from collections import defaultdict, deque
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings as app_settings
from app.db import SessionLocal
from app.security.service import (
    authenticate,
    client_ip_allowed,
    get_cached_security_policy,
    get_security_policy,
    host_allowed,
    normalize_request_host,
)

logger = logging.getLogger(__name__)

PUBLIC_PREFIXES = ("/healthz", "/readyz", "/static")
SECURITY_PREFIX = "/security"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

MAX_AUTHORIZATION_HEADER_LEN = 2048
MAX_BASIC_USERNAME_LEN = 64
MAX_BASIC_PASSWORD_LEN = 256
AUTH_FAIL_LIMIT = 10
AUTH_FAIL_WINDOW_SECONDS = 60.0

# ip -> deque of failure timestamps
_auth_failures: dict[str, deque[float]] = defaultdict(deque)


def is_public_path(path: str) -> bool:
    if path in ("/healthz", "/readyz"):
        return True
    return path.startswith("/static/")


def effective_client_ip(request: Request) -> str:
    """Use the direct peer address only — never trust forwarded headers."""
    if request.client and request.client.host:
        return request.client.host
    return ""


def origin_matches_request(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.netloc:
        return False
    expected_host = request.headers.get("host") or request.url.netloc
    expected_scheme = request.url.scheme
    return secrets.compare_digest(
        parsed.scheme.lower(), expected_scheme.lower()
    ) and secrets.compare_digest(parsed.netloc.lower(), expected_host.lower())


def _unauthorized() -> Response:
    return Response(
        content="Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="NetWatcher"'},
        media_type="text/plain",
    )


def _forbidden(message: str = "Forbidden") -> Response:
    return JSONResponse({"detail": message}, status_code=403)


def _too_many_requests() -> Response:
    return JSONResponse({"detail": "Too many authentication failures"}, status_code=429)


def _prune_failures(ip: str, now: float) -> deque[float]:
    bucket = _auth_failures[ip]
    cutoff = now - AUTH_FAIL_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if not bucket:
        _auth_failures.pop(ip, None)
        return _auth_failures[ip]
    return bucket


def auth_failures_exceeded(ip: str) -> bool:
    if not ip:
        return False
    now = time.monotonic()
    bucket = _prune_failures(ip, now)
    return len(bucket) >= AUTH_FAIL_LIMIT


def record_auth_failure(ip: str) -> None:
    if not ip:
        return
    now = time.monotonic()
    bucket = _prune_failures(ip, now)
    bucket.append(now)


def clear_auth_failures(ip: str) -> None:
    _auth_failures.pop(ip, None)


def reset_auth_failure_limiter() -> None:
    """Test helper: clear all in-memory auth failure state."""
    _auth_failures.clear()


def _parse_basic_auth(header: str | None) -> tuple[str, str] | None:
    if not header or not header.lower().startswith("basic "):
        return None
    if len(header) > MAX_AUTHORIZATION_HEADER_LEN:
        return None
    try:
        raw = base64.b64decode(header.split(" ", 1)[1].strip(), validate=True).decode("utf-8")
    except Exception:
        return None
    if ":" not in raw:
        return None
    username, password = raw.split(":", 1)
    if len(username) > MAX_BASIC_USERNAME_LEN or len(password) > MAX_BASIC_PASSWORD_LEN:
        return None
    return username, password


def _load_policy():
    cached = get_cached_security_policy()
    if cached is not None:
        return cached, None
    db = SessionLocal()
    try:
        return get_security_policy(db), db
    except Exception:
        db.close()
        raise


class AccessControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.client_ip = effective_client_ip(request)
        request.state.actor = "anonymous"

        if is_public_path(path):
            return await call_next(request)

        db = None
        try:
            sec, db = _load_policy()

            if sec.host_restriction_enabled:
                raw_host = request.headers.get("host") or request.url.hostname or ""
                if not host_allowed(
                    normalize_request_host(raw_host),
                    host_restriction_enabled=True,
                    allowed_hosts=list(sec.allowed_hosts),
                ):
                    return _forbidden("Host is not allowed")

            if request.method in MUTATING_METHODS and not origin_matches_request(request):
                return _forbidden("Origin mismatch")

            if sec.cidr_restriction_enabled and not app_settings.SECURITY_RECOVERY_BYPASS:
                client_ip = request.state.client_ip
                if not client_ip_allowed(client_ip, list(sec.allowed_cidrs)):
                    return _forbidden("Client IP is not allowed")

            requires_auth = path.startswith(SECURITY_PREFIX) or bool(sec.authentication_enabled)
            if requires_auth:
                client_ip = request.state.client_ip
                if auth_failures_exceeded(client_ip):
                    return _too_many_requests()

                creds = _parse_basic_auth(request.headers.get("authorization"))
                if not creds:
                    record_auth_failure(client_ip)
                    return _unauthorized()

                ok = await asyncio.to_thread(authenticate, sec, creds[0], creds[1])
                if not ok:
                    record_auth_failure(client_ip)
                    return _unauthorized()

                clear_auth_failures(client_ip)
                request.state.actor = creds[0]
            elif sec.authentication_enabled is False:
                request.state.actor = "anonymous"
        finally:
            if db is not None:
                db.close()

        return await call_next(request)
