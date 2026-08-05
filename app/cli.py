"""CLI utilities for NetWatcher operators."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet

from app.db import SessionLocal
from app.security.crypto_migrate import rekey_notification_secrets
from app.security.secrets import (
    SecretKeyError,
    default_secret_key_path,
    fernet_from_material,
    init_app_secrets,
    is_usable_env_secret,
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
    except ValueError, Exception:
        return fernet_from_material(secret.encode("utf-8"))


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
    if is_usable_env_secret(new_env):
        new_fernet = fernet_from_material(new_env.encode("utf-8"))
        source = "env"
    elif args.generate_file:
        generated = Fernet.generate_key()
        write_key_file(key_path, generated)
        new_fernet = Fernet(generated.strip())
        source = f"file:{key_path}"
    else:
        try:
            new_fernet, source = resolve_fernet(
                env_secret=new_env or None,
                key_path=key_path,
                allow_generate=False,
            )
        except SecretKeyError:
            print(
                "No usable new key. Set APP_SECRET_KEY, ensure the key file exists, "
                "or pass --generate-file.",
                file=sys.stderr,
            )
            return 2

    db = SessionLocal()
    try:
        count = rekey_notification_secrets(db, old_fernet=old_fernet, new_fernet=new_fernet)
    except Exception as exc:
        db.rollback()
        print(f"Rekey failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    reset_secrets_for_tests()
    init_app_secrets(env_secret=new_env or None, key_path=key_path, allow_generate=False)
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
