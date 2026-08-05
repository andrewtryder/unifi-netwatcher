"""Tests for notification config encryption and key lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.db import Base
from app.models import NotificationChannel
from app.security.crypto_migrate import migrate_notification_secrets, rekey_notification_secrets
from app.security.secrets import (
    ENC_PREFIX,
    SecretKeyError,
    decrypt_config,
    encrypt_config,
    init_app_secrets,
    reset_secrets_for_tests,
)
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def secret_db(tmp_path: Path):
    reset_secrets_for_tests()
    key_path = tmp_path / "app-secret.key"
    init_app_secrets(env_secret="", key_path=key_path, allow_generate=True)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()
    yield db, key_path
    db.close()
    Base.metadata.drop_all(bind=engine)
    reset_secrets_for_tests()


def test_encrypt_decrypt_roundtrip(secret_db):
    db, _ = secret_db
    payload = {"url": "https://hooks.example.com/x", "method": "POST"}
    token = encrypt_config(payload)
    assert token.startswith(ENC_PREFIX)
    assert decrypt_config(token) == payload


def test_migrate_plaintext_and_refuse_wrong_key(secret_db, tmp_path: Path):
    db, key_path = secret_db
    db.add(
        NotificationChannel(
            name="hook",
            type="webhook",
            enabled=True,
            config_json={"url": "https://hooks.example.com/hook", "method": "POST"},
        )
    )
    db.commit()

    result = migrate_notification_secrets(db)
    assert result["migrated"] == 1
    channel = db.query(NotificationChannel).one()
    assert isinstance(channel.config_json, str)
    assert channel.config_json.startswith(ENC_PREFIX)

    # Wrong key must not silently start
    reset_secrets_for_tests()
    other = tmp_path / "other.key"
    other.write_bytes(Fernet.generate_key() + b"\n")
    init_app_secrets(env_secret="", key_path=other, allow_generate=False)
    with pytest.raises(SecretKeyError):
        migrate_notification_secrets(db)


def test_rekey_notification_secrets(secret_db, tmp_path: Path):
    db, key_path = secret_db
    old = Fernet(key_path.read_bytes().strip())
    payload = {"token": "12345678", "user": "87654321"}
    db.add(
        NotificationChannel(
            name="push",
            type="pushover",
            enabled=True,
            config_json=encrypt_config(payload, fernet=old),
        )
    )
    db.commit()

    new_key = Fernet.generate_key()
    new_path = tmp_path / "new.key"
    new_path.write_bytes(new_key + b"\n")
    new_f = Fernet(new_key)
    count = rekey_notification_secrets(db, old_fernet=old, new_fernet=new_f)
    assert count == 1
    channel = db.query(NotificationChannel).one()
    assert decrypt_config(channel.config_json, fernet=new_f) == payload
