"""Migration applies cleanly on an empty SQLite database."""

from __future__ import annotations

from pathlib import Path

import alembic.command
import alembic.config
from sqlalchemy import create_engine, inspect, text


def test_alembic_upgrade_head_on_empty_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "migrate.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    # alembic.ini uses sqlalchemy.url from env.py; patch via env is enough if env reads DATABASE_URL
    cfg = alembic.config.Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    alembic.command.upgrade(cfg, "head")

    engine = create_engine(url)
    insp = inspect(engine)
    assert "devices" in insp.get_table_names()
    assert "security_settings" in insp.get_table_names()

    indexes = {idx["name"] for idx in insp.get_indexes("devices")}
    assert "ix_devices_status_last_seen_at" in indexes

    obs_indexes = {idx["name"] for idx in insp.get_indexes("observations")}
    assert "ix_observations_device_id_seen_at" in obs_indexes

    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        row = conn.execute(text("PRAGMA foreign_key_list(observations)")).mappings().all()
        assert any(r["on_delete"].upper() == "CASCADE" for r in row)

    cols = {c["name"] for c in insp.get_columns("security_settings")}
    assert "host_restriction_enabled" in cols
    assert "allowed_hosts" in cols


def test_alembic_upgrade_from_initial_revision(tmp_path: Path, monkeypatch):
    """Simulate a previous-release schema (initial revision) upgrading to head."""
    db_path = tmp_path / "legacy.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = alembic.config.Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    alembic.command.upgrade(cfg, "84d6a692d6a4")
    alembic.command.upgrade(cfg, "head")

    engine = create_engine(url)
    insp = inspect(engine)
    assert "security_settings" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("security_settings")}
    assert "host_restriction_enabled" in cols
