from datetime import datetime
from typing import TYPE_CHECKING, List

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User

EMBEDDING_DIM = 1536


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_phone: Mapped[str] = mapped_column(
        String(20), ForeignKey("users.phone_number", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    memory_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[List[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="memories")

    __table_args__ = (Index("ix_memories_category_user", "category", "user_phone"),)
