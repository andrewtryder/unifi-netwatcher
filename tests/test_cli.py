"""End-to-end tests for the operator CLI rekey command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from app.db import Base
from app.models import NotificationChannel
from app.security.crypto_migrate import migrate_notification_secrets
from app.security.secrets import (
    ENC_PREFIX,
    cleanup_key_rotation_backup,
    decrypt_config,
    encrypt_config,
    init_app_secrets,
    key_backup_path,
    key_staging_path,
    reset_secrets_for_tests,
    write_key_file,
)
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def rekey_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_secrets_for_tests()
    # Avoid conftest's APP_SECRET_KEY taking precedence over --generate-file.
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    monkeypatch.delenv("OLD_APP_SECRET_KEY", raising=False)
    key_path = tmp_path / "app-secret.key"
    old_key = Fernet.generate_key()
    write_key_file(key_path, old_key)
    old_fernet = Fernet(old_key.strip())

    engine = create_engine(
        f"sqlite:///{tmp_path / 'cli.db'}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    db = Session()
    payload = {"token": "12345678", "user": "87654321"}
    db.add(
        NotificationChannel(
            name="push",
            type="pushover",
            enabled=True,
            config_json=encrypt_config(payload, fernet=old_fernet),
        )
    )
    db.commit()
    db.close()

    monkeypatch.setattr("app.cli.SessionLocal", Session)
    yield {
        "key_path": key_path,
        "old_key": old_key.decode("ascii"),
        "old_fernet": old_fernet,
        "Session": Session,
        "payload": payload,
        "tmp_path": tmp_path,
    }
    Base.metadata.drop_all(bind=engine)
    reset_secrets_for_tests()


def test_rekey_generate_file_success(rekey_env):
    from app.cli import main

    key_path: Path = rekey_env["key_path"]
    active_before = key_path.read_bytes()
    rc = main(
        [
            "rekey",
            "--old-key",
            rekey_env["old_key"],
            "--key-path",
            str(key_path),
            "--generate-file",
        ]
    )
    assert rc == 0
    assert key_path.read_bytes() != active_before
    assert not key_staging_path(key_path).exists()
    assert not key_backup_path(key_path).exists()

    new_f = Fernet(key_path.read_bytes().strip())
    db = rekey_env["Session"]()
    try:
        channel = db.query(NotificationChannel).one()
        assert decrypt_config(channel.config_json, fernet=new_f) == rekey_env["payload"]
    finally:
        db.close()


def test_rekey_wrong_old_key_leaves_files_and_db(rekey_env):
    from app.cli import main

    key_path: Path = rekey_env["key_path"]
    active_before = key_path.read_bytes()
    # Fernet keys are URL-safe base64 and can start with '-' or '_'.
    # argparse would then treat the value as an unknown flag, causing sys.exit(2).
    # Re-generate until we get a key that doesn't start with '-'.
    while True:
        wrong = Fernet.generate_key().decode("ascii")
        if not wrong.startswith("-"):
            break
    rc = main(
        [
            "rekey",
            "--old-key",
            wrong,
            "--key-path",
            str(key_path),
            "--generate-file",
        ]
    )
    assert rc == 1
    assert key_path.read_bytes() == active_before
    assert not key_staging_path(key_path).exists()

    db = rekey_env["Session"]()
    try:
        channel = db.query(NotificationChannel).one()
        assert (
            decrypt_config(channel.config_json, fernet=rekey_env["old_fernet"])
            == rekey_env["payload"]
        )
    finally:
        db.close()


def test_rekey_transaction_failure_leaves_active_key(rekey_env):
    from app.cli import main

    key_path: Path = rekey_env["key_path"]
    active_before = key_path.read_bytes()

    def boom(*_a, **_k):
        raise RuntimeError("simulated commit failure")

    with patch("app.cli.rekey_notification_secrets", side_effect=boom):
        rc = main(
            [
                "rekey",
                "--old-key",
                rekey_env["old_key"],
                "--key-path",
                str(key_path),
                "--generate-file",
            ]
        )
    assert rc == 1
    assert key_path.read_bytes() == active_before
    assert not key_staging_path(key_path).exists()

    db = rekey_env["Session"]()
    try:
        channel = db.query(NotificationChannel).one()
        assert channel.config_json.startswith(ENC_PREFIX)
        assert (
            decrypt_config(channel.config_json, fernet=rekey_env["old_fernet"])
            == rekey_env["payload"]
        )
    finally:
        db.close()


def test_rekey_file_activate_failure_keeps_staging(rekey_env):
    from app.cli import main

    key_path: Path = rekey_env["key_path"]
    active_before = key_path.read_bytes()

    def boom_replace(src, dst):
        raise OSError("simulated replace failure")

    with patch("app.cli.os.replace", side_effect=boom_replace):
        rc = main(
            [
                "rekey",
                "--old-key",
                rekey_env["old_key"],
                "--key-path",
                str(key_path),
                "--generate-file",
            ]
        )
    assert rc == 1
    assert key_path.read_bytes() == active_before
    staging = key_staging_path(key_path)
    assert staging.is_file()

    # DB already re-encrypted with the staged key.
    new_f = Fernet(staging.read_bytes().strip())
    db = rekey_env["Session"]()
    try:
        channel = db.query(NotificationChannel).one()
        assert decrypt_config(channel.config_json, fernet=new_f) == rekey_env["payload"]
    finally:
        db.close()


def test_cleanup_rotation_backup_after_verified_startup(rekey_env):
    key_path: Path = rekey_env["key_path"]
    bak = key_backup_path(key_path)
    bak.write_bytes(b"old-backup\n")
    init_app_secrets(env_secret="", key_path=key_path, allow_generate=False)
    db = rekey_env["Session"]()
    try:
        migrate_notification_secrets(db)
        assert cleanup_key_rotation_backup(key_path) is True
        assert not bak.exists()
    finally:
        db.close()
