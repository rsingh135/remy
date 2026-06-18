"""Initial schema: users, events, memories with pgvector

Revision ID: 0001
Revises:
Create Date: 2026-06-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy import text

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1024  # Titan Text Embeddings v2 default output dimension


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("phone_number", sa.String(20), primary_key=True),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("timezone", sa.String(50), nullable=True),
        sa.Column("persona_style", sa.String(30), nullable=True),
        sa.Column("objective", sa.String(50), nullable=True),
        sa.Column("core_goal", sa.Text, nullable=True),
        sa.Column("onboarding_step", sa.Integer, nullable=False, server_default="0"),
        sa.Column("streak_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_paused", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_phone",
            sa.String(20),
            sa.ForeignKey("users.phone_number", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_events_user_phone_timestamp", "events", ["user_phone", "timestamp"])

    op.create_table(
        "memories",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_phone",
            sa.String(20),
            sa.ForeignKey("users.phone_number", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("memory_text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_memories_category_user", "memories", ["category", "user_phone"])

    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_memories_hnsw "
            "ON memories USING hnsw (embedding vector_cosine_ops) "
            "WITH (m=16, ef_construction=64)"
        )
    )


def downgrade() -> None:
    op.drop_table("memories")
    op.drop_table("events")
    op.drop_table("users")
    # vector extension is owned by the RDS master user — skip dropping it
