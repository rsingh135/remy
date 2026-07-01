"""widen phone_number columns to VARCHAR(100)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-30

"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "phone_number", type_=sa.String(100), existing_type=sa.String())
    op.alter_column("events", "user_phone", type_=sa.String(100), existing_type=sa.String())
    op.alter_column("memories", "user_phone", type_=sa.String(100), existing_type=sa.String())
    op.alter_column("user_google_tokens", "user_phone", type_=sa.String(100), existing_type=sa.String())


def downgrade() -> None:
    op.alter_column("user_google_tokens", "user_phone", type_=sa.String(), existing_type=sa.String(100))
    op.alter_column("memories", "user_phone", type_=sa.String(), existing_type=sa.String(100))
    op.alter_column("events", "user_phone", type_=sa.String(), existing_type=sa.String(100))
    op.alter_column("users", "phone_number", type_=sa.String(), existing_type=sa.String(100))
