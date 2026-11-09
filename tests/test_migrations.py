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

    device_cols = {c["name"] for c in insp.get_columns("devices")}
    assert "connectivity_state" in device_cols
    assert "last_connected_at" in device_cols
    assert "last_disconnected_at" in device_cols
    assert "consecutive_missed_scans" in device_cols
    assert "offline_candidate_since" in device_cols

    device_indexes = {idx["name"] for idx in insp.get_indexes("devices")}
    assert "ix_devices_connectivity_state" in device_indexes


def test_migration_connectivity_backfill_and_reversibility(tmp_path: Path, monkeypatch):
    """Test upgrading a database with existing devices and downgrading."""
    db_path = tmp_path / "devices_migrate.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = alembic.config.Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)

    # Upgrade to the revision immediately prior to connectivity tracking
    alembic.command.upgrade(cfg, "f6a7b8c9d0e1")

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO devices (mac, hostname, ip, status, created_at, updated_at) "
                "VALUES ('aa:bb:cc:dd:ee:ff', 'pre-existing-device', '192.168.1.50', 'trusted', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    # Upgrade to head (adds connectivity columns and constraints)
    alembic.command.upgrade(cfg, "head")

    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT mac, status, connectivity_state, consecutive_missed_scans FROM devices WHERE mac = 'aa:bb:cc:dd:ee:ff'"
                )
            )
            .mappings()
            .one()
        )
        assert row["status"] == "trusted"
        assert row["connectivity_state"] == "unknown"
        assert row["consecutive_missed_scans"] == 0

    # Downgrade to prior revision
    alembic.command.downgrade(cfg, "f6a7b8c9d0e1")
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("devices")}
    assert "connectivity_state" not in cols

    # Upgrade back to head
    alembic.command.upgrade(cfg, "head")
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("devices")}
    assert "connectivity_state" in cols
