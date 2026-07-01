"""add gmail_read_enabled flag to users

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-01

"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("gmail_read_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "gmail_read_enabled")
