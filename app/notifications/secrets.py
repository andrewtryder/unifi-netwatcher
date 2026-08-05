"""Encrypt and redact notification channel secrets at rest."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

ENC_PREFIX = "enc:v1:"

_SECRET_KEYS = frozenset({"token", "user", "password", "api_key", "authorization", "headers"})


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.APP_SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_channel_config(config: dict[str, Any]) -> str:
    """Serialize and encrypt channel config for SQLite storage."""
    payload = json.dumps(config, separators=(",", ":"), sort_keys=True).encode("utf-8")
    token = _fernet().encrypt(payload).decode("ascii")
    return f"{ENC_PREFIX}{token}"


def decrypt_channel_config(stored: str) -> dict[str, Any]:
    """Decrypt stored config; plaintext JSON remains supported for migration."""
    if stored.startswith(ENC_PREFIX):
        token = stored[len(ENC_PREFIX) :]
        try:
            raw = _fernet().decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt notification channel config") from exc
        return json.loads(raw.decode("utf-8"))

    # Legacy plaintext rows
    return json.loads(stored)


def redact_config_for_log(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy safe for events/logs (secrets replaced)."""
    out: dict[str, Any] = {}
    for key, value in config.items():
        lowered = key.lower()
        if lowered in _SECRET_KEYS or lowered.endswith("_token") or lowered.endswith("_key"):
            out[key] = "[redacted]"
        elif isinstance(value, dict):
            out[key] = redact_config_for_log(value)
        else:
            out[key] = value
    return out
