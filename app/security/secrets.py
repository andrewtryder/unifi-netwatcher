"""Application secret resolution and Fernet helpers for notification configs."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENC_PREFIX = "enc:v1:"
_PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "change-me",
        "changeme",
        "secret",
        "password",
        "admin",
        "default",
        "your-secret-key",
        "replace-me",
    }
)

_fernet: Fernet | None = None
_secret_source: str | None = None


class SecretKeyError(RuntimeError):
    """Raised when the application secret cannot be resolved safely."""


def default_secret_key_path() -> Path:
    override = os.environ.get("APP_SECRET_KEY_PATH", "").strip()
    if override:
        return Path(override)
    return Path("data") / "app-secret.key"


def is_usable_env_secret(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.lower() in _PLACEHOLDER_SECRETS:
        return False
    return len(stripped) >= 16


def fernet_from_material(material: bytes) -> Fernet:
    """Derive a url-safe Fernet key from arbitrary secret material."""
    digest = hashlib.sha256(material).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def write_key_file(path: Path, key: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key + (b"\n" if not key.endswith(b"\n") else b""))
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("Could not set mode 0600 on %s", path)


def _load_key_file(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    raw = path.read_bytes().strip()
    return raw or None


def resolve_fernet(
    *,
    env_secret: str | None = None,
    key_path: Path | None = None,
    allow_generate: bool = True,
) -> tuple[Fernet, str]:
    """Resolve Fernet + human-readable source.

    Order: explicit usable APP_SECRET_KEY → key file → generate (if allowed).
    """
    path = key_path or default_secret_key_path()
    if env_secret is None:
        env_secret = os.environ.get("APP_SECRET_KEY", "")

    if is_usable_env_secret(env_secret or ""):
        return fernet_from_material(env_secret.strip().encode("utf-8")), "env"

    file_key = _load_key_file(path)
    if file_key is not None:
        # Accept either raw Fernet token (url-safe base64) or arbitrary bytes.
        try:
            return Fernet(file_key.splitlines()[0].strip()), "file"
        except Exception:
            return fernet_from_material(file_key), "file"

    if not allow_generate:
        raise SecretKeyError(
            f"No APP_SECRET_KEY and no key file at {path}. "
            "Provide APP_SECRET_KEY or run once to generate a key file."
        )

    generated = Fernet.generate_key()
    write_key_file(path, generated)
    logger.info("Created Fernet key file at %s (mode 0600)", path)
    return Fernet(generated.strip()), "generated"


def init_app_secrets(
    *,
    env_secret: str | None = None,
    key_path: Path | None = None,
    allow_generate: bool = True,
) -> Fernet:
    global _fernet, _secret_source
    fernet, source = resolve_fernet(
        env_secret=env_secret, key_path=key_path, allow_generate=allow_generate
    )
    _fernet = fernet
    _secret_source = source
    return fernet


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        init_app_secrets()
    assert _fernet is not None
    return _fernet


def secret_source() -> str | None:
    return _secret_source


def reset_secrets_for_tests() -> None:
    global _fernet, _secret_source
    _fernet = None
    _secret_source = None


def is_encrypted_config(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt_config(config: dict[str, Any], *, fernet: Fernet | None = None) -> str:
    token = (fernet or get_fernet()).encrypt(
        json.dumps(config, separators=(",", ":")).encode("utf-8")
    )
    return f"{ENC_PREFIX}{token.decode('ascii')}"


def decrypt_config(value: Any, *, fernet: Fernet | None = None) -> dict[str, Any]:
    """Return a plaintext config dict from stored JSON (encrypted or legacy)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if value.startswith(ENC_PREFIX):
            token = value[len(ENC_PREFIX) :].encode("ascii")
            try:
                raw = (fernet or get_fernet()).decrypt(token)
            except InvalidToken as exc:
                raise SecretKeyError(
                    "Unable to decrypt notification configuration with the current "
                    "application secret. Restore the previous APP_SECRET_KEY / "
                    "data/app-secret.key or run the rekey command."
                ) from exc
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("Decrypted notification config must be a JSON object")
            return parsed
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("Notification config must be a JSON object")
        return parsed
    raise ValueError("Notification config must be a JSON object or encrypted string")


def can_decrypt(value: Any, *, fernet: Fernet | None = None) -> bool:
    if not is_encrypted_config(value):
        return True
    try:
        decrypt_config(value, fernet=fernet)
        return True
    except SecretKeyError, ValueError, json.JSONDecodeError:
        return False
