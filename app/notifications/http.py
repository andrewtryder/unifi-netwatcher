"""Shared httpx client for outbound notification delivery."""

from __future__ import annotations

import httpx

_client: httpx.Client | None = None

MAX_RESPONSE_BYTES = 64 * 1024


def get_notification_http_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=10.0,
            trust_env=False,
            follow_redirects=False,
        )
    return _client


def close_notification_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        _client.close()
    _client = None


def read_capped_response_text(response: httpx.Response, *, limit: int = MAX_RESPONSE_BYTES) -> str:
    """Read at most ``limit`` bytes from the response body, then discard the rest."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        remaining = limit - total
        if remaining <= 0:
            # Drain remaining body without retaining it.
            continue
        if len(chunk) <= remaining:
            chunks.append(chunk)
            total += len(chunk)
        else:
            chunks.append(chunk[:remaining])
            total = limit
    return b"".join(chunks).decode("utf-8", errors="replace")
