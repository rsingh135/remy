from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.event import Event
    from app.models.google_token import UserGoogleToken
    from app.models.memory import Memory


class User(Base):
    __tablename__ = "users"

    contact_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(100))
    timezone: Mapped[Optional[str]] = mapped_column(String(50))
    persona_style: Mapped[Optional[str]] = mapped_column(String(30))
    objective: Mapped[Optional[str]] = mapped_column(String(50))
    core_goal: Mapped[Optional[str]] = mapped_column(Text)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0)
    streak_count: Mapped[int] = mapped_column(Integer, default=0)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    gmail_read_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    events: Mapped[List["Event"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    memories: Mapped[List["Memory"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    google_token: Mapped[Optional["UserGoogleToken"]] = relationship(
        "UserGoogleToken", back_populates="user", cascade="all, delete-orphan", uselist=False
    )
