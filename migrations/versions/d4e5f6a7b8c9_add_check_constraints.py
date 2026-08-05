"""Add CHECK constraints and document JSON column typing.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-04 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite stores JSON as TEXT; recreate tables to attach CHECK constraints.
    with op.batch_alter_table("devices") as batch_op:
        batch_op.create_check_constraint(
            "ck_devices_status",
            "status IN ('unknown','trusted','ignored','blocked')",
        )

    with op.batch_alter_table("events") as batch_op:
        batch_op.create_check_constraint(
            "ck_events_severity",
            "severity IN ('info','warning','error')",
        )

    with op.batch_alter_table("notification_channels") as batch_op:
        batch_op.create_check_constraint(
            "ck_notification_channels_type",
            "type IN ('webhook','pushover')",
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_channels") as batch_op:
        batch_op.drop_constraint("ck_notification_channels_type", type_="check")
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_constraint("ck_events_severity", type_="check")
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_constraint("ck_devices_status", type_="check")
