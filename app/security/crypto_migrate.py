"""Migrate and rekey encrypted notification channel configurations."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import NotificationChannel
from app.security.secrets import (
    SecretKeyError,
    can_decrypt,
    decrypt_config,
    encrypt_config,
    get_fernet,
    is_encrypted_config,
)

logger = logging.getLogger(__name__)


def load_channel_config(channel: NotificationChannel) -> dict[str, Any]:
    return decrypt_config(channel.config_json)


def store_channel_config(channel: NotificationChannel, config: dict[str, Any]) -> None:
    channel.config_json = encrypt_config(config)


def migrate_notification_secrets(db: Session) -> dict[str, int]:
    """Encrypt plaintext configs; verify encrypted rows decrypt with current key.

    Never generates a replacement key here — callers must resolve the key first.
    Aborts if any encrypted row cannot be decrypted with the active key.
    """
    fernet = get_fernet()
    channels = db.query(NotificationChannel).all()
    encrypted_ok = 0
    migrated = 0
    already = 0

    for channel in channels:
        value = channel.config_json
        if is_encrypted_config(value):
            if not can_decrypt(value, fernet=fernet):
                raise SecretKeyError(
                    f"Notification channel id={channel.id} name={channel.name!r} is "
                    "encrypted but cannot be decrypted with the current application "
                    "secret. Refusing to start with a different key. Restore the "
                    "previous key or run: uv run python -m app.cli rekey"
                )
            encrypted_ok += 1
            already += 1
            continue

        try:
            plaintext = decrypt_config(value, fernet=fernet)
        except (ValueError, TypeError) as exc:
            raise SecretKeyError(
                f"Notification channel id={channel.id} has unparseable config: {exc}"
            ) from exc

        channel.config_json = encrypt_config(plaintext, fernet=fernet)
        migrated += 1

    if migrated:
        db.commit()
        logger.info("Encrypted %s plaintext notification channel config(s)", migrated)
    return {
        "migrated": migrated,
        "already_encrypted": already,
        "verified_encrypted": encrypted_ok,
        "total": len(channels),
    }


def verify_channels_with_fernet(db: Session, fernet) -> int:
    """Ensure every channel config decrypts/parses with the given Fernet. No writes."""
    channels = db.query(NotificationChannel).all()
    for channel in channels:
        try:
            decrypt_config(channel.config_json, fernet=fernet)
        except SecretKeyError as exc:
            raise SecretKeyError(
                f"Notification channel id={channel.id} name={channel.name!r} cannot "
                "be decrypted with the provided key."
            ) from exc
        except (ValueError, TypeError) as exc:
            raise SecretKeyError(
                f"Notification channel id={channel.id} has unparseable config: {exc}"
            ) from exc
    return len(channels)


def rekey_notification_secrets(
    db: Session,
    *,
    old_fernet,
    new_fernet,
) -> int:
    """Decrypt all channels with old key and re-encrypt with new key in one transaction."""
    channels = db.query(NotificationChannel).all()
    count = 0
    for channel in channels:
        plaintext = decrypt_config(channel.config_json, fernet=old_fernet)
        channel.config_json = encrypt_config(plaintext, fernet=new_fernet)
        count += 1
    db.commit()
    return count
