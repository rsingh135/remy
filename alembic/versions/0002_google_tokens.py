"""Add user_google_tokens table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_google_tokens",
        sa.Column(
            "user_phone",
            sa.String(20),
            sa.ForeignKey("users.phone_number", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scopes", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_google_tokens")
