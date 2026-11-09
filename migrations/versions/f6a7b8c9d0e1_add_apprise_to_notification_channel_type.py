"""Add apprise to notification_channels type check constraint.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-09 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notification_channels") as batch_op:
        batch_op.drop_constraint("ck_notification_channels_type", type_="check")
        batch_op.create_check_constraint(
            "ck_notification_channels_type",
            "type IN ('webhook','pushover','apprise')",
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_channels") as batch_op:
        batch_op.drop_constraint("ck_notification_channels_type", type_="check")
        batch_op.create_check_constraint(
            "ck_notification_channels_type",
            "type IN ('webhook','pushover')",
        )
