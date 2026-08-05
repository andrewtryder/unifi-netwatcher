"""Add query indexes and CASCADE foreign keys.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-04 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _recreate_table(
    table_name: str,
    columns: list,
    fks: list,
    index_defs: list[tuple],
    copy_columns: list[str],
) -> None:
    tmp = f"_{table_name}_new"
    op.create_table(tmp, *columns, *fks)
    cols = ", ".join(copy_columns)
    op.execute(sa.text(f"INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {table_name}"))
    op.drop_table(table_name)
    op.rename_table(tmp, table_name)
    for name, cols_list in index_defs:
        op.create_index(name, table_name, cols_list, unique=False)


def upgrade() -> None:
    # SQLite cannot ALTER FK ondelete; recreate tables with PRAGMA off.
    op.execute(sa.text("PRAGMA foreign_keys=OFF"))

    _recreate_table(
        "observations",
        [
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("device_id", sa.Integer(), nullable=True),
            sa.Column("mac", sa.String(), nullable=False),
            sa.Column("ip", sa.String(), nullable=True),
            sa.Column("hostname", sa.String(), nullable=True),
            sa.Column("site", sa.String(), nullable=True),
            sa.Column("ssid", sa.String(), nullable=True),
            sa.Column("ap_mac", sa.String(), nullable=True),
            sa.Column("raw_json", sa.String(), nullable=True),
            sa.Column("seen_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        ],
        [
            sa.ForeignKeyConstraint(
                ["device_id"],
                ["devices.id"],
                name="fk_observations_device_id_devices",
                ondelete="CASCADE",
            ),
        ],
        [
            ("ix_observations_id", ["id"]),
            ("ix_observations_device_id_seen_at", ["device_id", "seen_at"]),
        ],
        ["id", "device_id", "mac", "ip", "hostname", "site", "ssid", "ap_mac", "raw_json", "seen_at"],
    )

    _recreate_table(
        "events",
        [
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("device_id", sa.Integer(), nullable=True),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("severity", sa.String(), nullable=False),
            sa.Column("message", sa.String(), nullable=True),
            sa.Column("metadata_json", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        ],
        [
            sa.ForeignKeyConstraint(
                ["device_id"],
                ["devices.id"],
                name="fk_events_device_id_devices",
                ondelete="CASCADE",
            ),
        ],
        [
            ("ix_events_id", ["id"]),
            (
                "ix_events_device_id_event_type_created_at",
                ["device_id", "event_type", "created_at"],
            ),
            ("ix_events_event_type_created_at", ["event_type", "created_at"]),
        ],
        ["id", "device_id", "event_type", "severity", "message", "metadata_json", "created_at"],
    )

    _recreate_table(
        "notification_deliveries",
        [
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_id", sa.Integer(), nullable=True),
            sa.Column("channel_id", sa.Integer(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=True),
            sa.Column("response", sa.String(), nullable=True),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        ],
        [
            sa.ForeignKeyConstraint(
                ["event_id"],
                ["events.id"],
                name="fk_notification_deliveries_event_id_events",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["channel_id"],
                ["notification_channels.id"],
                name="fk_notification_deliveries_channel_id_notification_channels",
                ondelete="CASCADE",
            ),
        ],
        [
            ("ix_notification_deliveries_id", ["id"]),
            ("ix_notification_deliveries_event_id", ["event_id"]),
            ("ix_notification_deliveries_channel_id", ["channel_id"]),
        ],
        ["id", "event_id", "channel_id", "success", "status_code", "response", "error", "created_at"],
    )

    op.create_index(
        "ix_devices_status_last_seen_at", "devices", ["status", "last_seen_at"], unique=False
    )
    op.execute(sa.text("PRAGMA foreign_keys=ON"))


def downgrade() -> None:
    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.drop_index("ix_devices_status_last_seen_at", table_name="devices")

    _recreate_table(
        "notification_deliveries",
        [
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("event_id", sa.Integer(), nullable=True),
            sa.Column("channel_id", sa.Integer(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=True),
            sa.Column("response", sa.String(), nullable=True),
            sa.Column("error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        ],
        [
            sa.ForeignKeyConstraint(["event_id"], ["events.id"]),
            sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"]),
        ],
        [("ix_notification_deliveries_id", ["id"])],
        ["id", "event_id", "channel_id", "success", "status_code", "response", "error", "created_at"],
    )

    _recreate_table(
        "events",
        [
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("device_id", sa.Integer(), nullable=True),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("severity", sa.String(), nullable=False),
            sa.Column("message", sa.String(), nullable=True),
            sa.Column("metadata_json", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        ],
        [sa.ForeignKeyConstraint(["device_id"], ["devices.id"])],
        [("ix_events_id", ["id"])],
        ["id", "device_id", "event_type", "severity", "message", "metadata_json", "created_at"],
    )

    _recreate_table(
        "observations",
        [
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("device_id", sa.Integer(), nullable=True),
            sa.Column("mac", sa.String(), nullable=False),
            sa.Column("ip", sa.String(), nullable=True),
            sa.Column("hostname", sa.String(), nullable=True),
            sa.Column("site", sa.String(), nullable=True),
            sa.Column("ssid", sa.String(), nullable=True),
            sa.Column("ap_mac", sa.String(), nullable=True),
            sa.Column("raw_json", sa.String(), nullable=True),
            sa.Column("seen_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        ],
        [sa.ForeignKeyConstraint(["device_id"], ["devices.id"])],
        [("ix_observations_id", ["id"])],
        ["id", "device_id", "mac", "ip", "hostname", "site", "ssid", "ap_mac", "raw_json", "seen_at"],
    )
    op.execute(sa.text("PRAGMA foreign_keys=ON"))
