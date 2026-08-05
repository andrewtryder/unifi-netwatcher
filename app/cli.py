"""CLI utilities for NetWatcher operators."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from cryptography.fernet import Fernet

from app.db import SessionLocal
from app.security.crypto_migrate import rekey_notification_secrets, verify_channels_with_fernet
from app.security.secrets import (
    SecretKeyError,
    default_secret_key_path,
    fernet_from_material,
    get_fernet,
    init_app_secrets,
    is_usable_env_secret,
    key_backup_path,
    key_staging_path,
    reset_secrets_for_tests,
    resolve_fernet,
    write_key_file,
)


def _fernet_from_secret_string(secret: str) -> Fernet:
    secret = secret.strip()
    if not secret:
        raise SecretKeyError("Empty secret")
    try:
        return Fernet(secret.encode("utf-8"))
    except Exception:
        return fernet_from_material(secret.encode("utf-8"))


def _activate_staged_key(key_path: Path, staging_path: Path) -> Path | None:
    """Backup active key (if any), atomically replace with staging. Returns bak path."""
    bak: Path | None = None
    if key_path.is_file():
        bak = key_backup_path(key_path)
        shutil.copy2(key_path, bak)
        try:
            os.chmod(bak, 0o600)
        except OSError:
            pass
    os.replace(staging_path, key_path)
    return bak


def cmd_rekey(args: argparse.Namespace) -> int:
    old_secret = (args.old_key or os.environ.get("OLD_APP_SECRET_KEY") or "").strip()
    if not old_secret:
        print(
            "Provide --old-key or set OLD_APP_SECRET_KEY to the previous secret "
            "(APP_SECRET_KEY value or Fernet key string).",
            file=sys.stderr,
        )
        return 2

    key_path = Path(args.key_path) if args.key_path else default_secret_key_path()
    old_fernet = _fernet_from_secret_string(old_secret)

    new_env = (args.new_key or os.environ.get("APP_SECRET_KEY") or "").strip()
    staging_path: Path | None = None
    rotate_file = False
    source: str

    if is_usable_env_secret(new_env):
        new_fernet = fernet_from_material(new_env.encode("utf-8"))
        source = "env"
    elif args.generate_file:
        generated = Fernet.generate_key()
        staging_path = key_staging_path(key_path)
        write_key_file(staging_path, generated)
        new_fernet = Fernet(generated.strip())
        rotate_file = True
        source = f"file:{key_path}"
    else:
        try:
            new_fernet, resolved = resolve_fernet(
                env_secret=new_env or None,
                key_path=key_path,
                allow_generate=False,
            )
            source = resolved
        except SecretKeyError:
            print(
                "No usable new key. Set APP_SECRET_KEY, ensure the key file exists, "
                "or pass --generate-file.",
                file=sys.stderr,
            )
            return 2

    db = SessionLocal()
    try:
        try:
            verify_channels_with_fernet(db, old_fernet)
            count = rekey_notification_secrets(db, old_fernet=old_fernet, new_fernet=new_fernet)
        except Exception as exc:
            db.rollback()
            if staging_path is not None and staging_path.is_file():
                try:
                    staging_path.unlink()
                except OSError:
                    pass
            print(f"Rekey failed: {exc}", file=sys.stderr)
            return 1
    finally:
        db.close()

    bak: Path | None = None
    if rotate_file:
        assert staging_path is not None
        try:
            bak = _activate_staged_key(key_path, staging_path)
        except OSError as exc:
            print(
                f"Database re-encrypted, but activating the new key file failed: {exc}. "
                f"New key remains at {staging_path}. Active file unchanged.",
                file=sys.stderr,
            )
            return 1

    reset_secrets_for_tests()
    try:
        init_app_secrets(
            env_secret=new_env if is_usable_env_secret(new_env) else "",
            key_path=key_path,
            allow_generate=False,
        )
        verify_db = SessionLocal()
        try:
            verify_channels_with_fernet(verify_db, get_fernet())
        finally:
            verify_db.close()
    except Exception as exc:
        print(f"Post-rekey verification failed: {exc}", file=sys.stderr)
        if bak is not None and bak.is_file():
            print(
                f"Recovery backup retained at {bak}. Restore it over {key_path} if needed.",
                file=sys.stderr,
            )
        return 1

    if bak is not None and bak.is_file():
        try:
            bak.unlink()
        except OSError:
            print(f"Warning: could not remove backup {bak}", file=sys.stderr)

    print(f"Re-encrypted {count} notification channel(s) using new key ({source}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="NetWatcher operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    rekey = sub.add_parser(
        "rekey",
        help="Re-encrypt notification configs from an old secret to the current secret",
    )
    rekey.add_argument(
        "--old-key",
        default="",
        help="Previous APP_SECRET_KEY or Fernet key (or set OLD_APP_SECRET_KEY)",
    )
    rekey.add_argument(
        "--new-key",
        default="",
        help="New APP_SECRET_KEY (defaults to env APP_SECRET_KEY)",
    )
    rekey.add_argument(
        "--key-path",
        default="",
        help="Path to app-secret.key (default: data/app-secret.key)",
    )
    rekey.add_argument(
        "--generate-file",
        action="store_true",
        help="Generate a new data/app-secret.key and re-encrypt to it",
    )
    rekey.set_defaults(func=cmd_rekey)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
