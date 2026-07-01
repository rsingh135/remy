"""rename phone_number/user_phone columns to contact_id/user_contact_id

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-01

"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "phone_number", new_column_name="contact_id",
                    type_=sa.String(255), existing_type=sa.String(100))
    op.alter_column("events", "user_phone", new_column_name="user_contact_id",
                    type_=sa.String(255), existing_type=sa.String(100))
    op.alter_column("memories", "user_phone", new_column_name="user_contact_id",
                    type_=sa.String(255), existing_type=sa.String(100))
    op.alter_column("user_google_tokens", "user_phone", new_column_name="user_contact_id",
                    type_=sa.String(255), existing_type=sa.String(100))

    op.drop_index("ix_events_user_phone_timestamp", table_name="events")
    op.create_index("ix_events_user_contact_id_timestamp", "events", ["user_contact_id", "timestamp"])

    op.drop_index("ix_memories_category_user", table_name="memories")
    op.create_index("ix_memories_category_user", "memories", ["category", "user_contact_id"])


def downgrade() -> None:
    op.drop_index("ix_memories_category_user", table_name="memories")
    op.create_index("ix_memories_category_user", "memories", ["category", "user_phone"])

    op.drop_index("ix_events_user_contact_id_timestamp", table_name="events")
    op.create_index("ix_events_user_phone_timestamp", "events", ["user_phone", "timestamp"])

    op.alter_column("user_google_tokens", "user_contact_id", new_column_name="user_phone",
                    type_=sa.String(100), existing_type=sa.String(255))
    op.alter_column("memories", "user_contact_id", new_column_name="user_phone",
                    type_=sa.String(100), existing_type=sa.String(255))
    op.alter_column("events", "user_contact_id", new_column_name="user_phone",
                    type_=sa.String(100), existing_type=sa.String(255))
    op.alter_column("users", "contact_id", new_column_name="phone_number",
                    type_=sa.String(100), existing_type=sa.String(255))
