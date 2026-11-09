"""Add timeline events index for device event queries.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-09 13:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_events_device_id_created_at",
        "events",
        ["device_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_events_device_id_created_at", table_name="events")

