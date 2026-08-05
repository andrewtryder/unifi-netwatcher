"""Add device telemetry columns from UniFi stat/sta

Revision ID: a1b2c3d4e5f6
Revises: 2c9c9eb928c3
Create Date: 2026-08-04 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "2c9c9eb928c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("name", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("is_wired", sa.Boolean(), nullable=True))
    op.add_column("devices", sa.Column("radio_proto", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("channel", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("rssi", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("satisfaction", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("tx_rate_bps", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("rx_rate_bps", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("tx_bytes_r", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("rx_bytes_r", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("sw_port", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("dev_cat", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("dev_family", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("dev_vendor", sa.String(), nullable=True))
    op.add_column("devices", sa.Column("os_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "os_name")
    op.drop_column("devices", "dev_vendor")
    op.drop_column("devices", "dev_family")
    op.drop_column("devices", "dev_cat")
    op.drop_column("devices", "sw_port")
    op.drop_column("devices", "rx_bytes_r")
    op.drop_column("devices", "tx_bytes_r")
    op.drop_column("devices", "rx_rate_bps")
    op.drop_column("devices", "tx_rate_bps")
    op.drop_column("devices", "satisfaction")
    op.drop_column("devices", "rssi")
    op.drop_column("devices", "channel")
    op.drop_column("devices", "radio_proto")
    op.drop_column("devices", "is_wired")
    op.drop_column("devices", "name")
