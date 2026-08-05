"""Content-Security-Policy helpers."""

from __future__ import annotations

import secrets

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send


def new_csp_nonce() -> str:
    return secrets.token_urlsafe(16)


def csp_header(nonce: str) -> str:
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )


class CSPMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nonce = new_csp_nonce()
        scope.setdefault("state", {})
        # Starlette Request.state is populated from scope["state"]
        request = Request(scope)
        request.state.csp_nonce = nonce

        async def send_with_csp(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"content-security-policy", csp_header(nonce).encode("latin-1")))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"no-referrer"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_csp)
