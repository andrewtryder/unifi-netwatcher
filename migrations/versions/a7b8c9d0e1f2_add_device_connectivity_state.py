"""Add device connectivity state and behavioral tracking columns.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "connectivity_state",
                sa.String(),
                server_default="unknown",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_disconnected_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "consecutive_missed_scans",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("offline_candidate_since", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "ix_devices_connectivity_state",
            ["connectivity_state"],
        )
        batch_op.create_check_constraint(
            "ck_devices_connectivity_state",
            "connectivity_state IN ('unknown','online','offline')",
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_constraint("ck_devices_connectivity_state", type_="check")
        batch_op.drop_index("ix_devices_connectivity_state")
        batch_op.drop_column("offline_candidate_since")
        batch_op.drop_column("consecutive_missed_scans")
        batch_op.drop_column("last_disconnected_at")
        batch_op.drop_column("last_connected_at")
        batch_op.drop_column("connectivity_state")
