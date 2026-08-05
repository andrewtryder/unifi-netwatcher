"""Add security_settings singleton table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("authentication_enabled", sa.Boolean(), nullable=False),
        sa.Column("authentication_username", sa.String(), nullable=False),
        sa.Column("authentication_password_hash", sa.String(), nullable=False),
        sa.Column("default_credentials_active", sa.Boolean(), nullable=False),
        sa.Column("cidr_restriction_enabled", sa.Boolean(), nullable=False),
        sa.Column("allowed_cidrs", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("security_settings")
