"""Shared httpx client for outbound notification delivery."""

from __future__ import annotations

import time

import httpx

_client: httpx.Client | None = None

MAX_RESPONSE_BYTES = 64 * 1024
# Overall wall-clock budget for reading a notification response body.
RESPONSE_READ_DEADLINE_SECONDS = 10.0


class ResponseBodyLimitExceeded(RuntimeError):
    """Raised when a notification response exceeds the retained-byte cap."""


class ResponseReadDeadlineExceeded(RuntimeError):
    """Raised when reading a notification response exceeds the overall deadline."""


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


def read_capped_response_text(
    response: httpx.Response,
    *,
    limit: int = MAX_RESPONSE_BYTES,
    deadline_seconds: float = RESPONSE_READ_DEADLINE_SECONDS,
) -> str:
    """Read at most ``limit`` bytes; stop and close when the cap or deadline is hit."""
    chunks: list[bytes] = []
    total = 0
    deadline = time.monotonic() + deadline_seconds
    try:
        for chunk in response.iter_bytes():
            if time.monotonic() > deadline:
                raise ResponseReadDeadlineExceeded(
                    f"Response read exceeded {deadline_seconds:.0f}s deadline"
                )
            if not chunk:
                continue
            remaining = limit - total
            if remaining <= 0 or len(chunk) > remaining:
                raise ResponseBodyLimitExceeded(f"Response body exceeds {limit} byte limit")
            chunks.append(chunk)
            total += len(chunk)
    finally:
        try:
            response.close()
        except Exception:
            pass
    return b"".join(chunks).decode("utf-8", errors="replace")
