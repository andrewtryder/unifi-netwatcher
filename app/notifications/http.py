"""Shared httpx client for outbound notification delivery."""

from __future__ import annotations

import httpx

_MAX_RESPONSE_BYTES = 4096

_client: httpx.Client | None = None


def get_notification_http_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        )
    return _client


def close_notification_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        _client.close()
    _client = None


def truncate_response_text(text: str, limit: int = _MAX_RESPONSE_BYTES) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
