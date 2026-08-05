"""Centralized HTTP Basic auth, CIDR filtering, same-origin checks, and CSP."""

from __future__ import annotations

import base64
import logging
import secrets
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings as app_settings
from app.db import SessionLocal
from app.security.service import (
    authenticate,
    client_ip_allowed,
    get_security_policy,
)

logger = logging.getLogger(__name__)

PUBLIC_PREFIXES = ("/healthz", "/readyz", "/static")
SECURITY_PREFIX = "/security"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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
    """Require an Origin header that exactly matches this request's host/scheme."""
    origin = request.headers.get("origin")
    if not origin:
        return False
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


def _parse_basic_auth(header: str | None) -> tuple[str, str] | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(header.split(" ", 1)[1].strip()).decode("utf-8")
    except Exception:
        return None
    if ":" not in raw:
        return None
    username, password = raw.split(":", 1)
    return username, password


def _csp_header(nonce: str) -> str:
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "object-src 'none'"
    )


class AccessControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        if is_public_path(path):
            response = await call_next(request)
            response.headers.setdefault("Content-Security-Policy", _csp_header(nonce))
            return response

        if request.method in MUTATING_METHODS and not origin_matches_request(request):
            return _forbidden("Origin mismatch")

        db = SessionLocal()
        try:
            sec = get_security_policy(db)

            if sec.cidr_restriction_enabled and not app_settings.SECURITY_RECOVERY_BYPASS:
                client_ip = effective_client_ip(request)
                if not client_ip_allowed(client_ip, list(sec.allowed_cidrs)):
                    return _forbidden("Client IP is not allowed")

            requires_auth = path.startswith(SECURITY_PREFIX) or bool(sec.authentication_enabled)
            if requires_auth:
                creds = _parse_basic_auth(request.headers.get("authorization"))
                if not creds or not authenticate(sec, creds[0], creds[1]):
                    return _unauthorized()
        finally:
            db.close()

        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _csp_header(nonce))
        return response
