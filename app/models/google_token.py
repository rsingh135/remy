import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class UserGoogleToken(Base):
    __tablename__ = "user_google_tokens"

    user_phone = Column(
        String(20),
        ForeignKey("users.phone_number", ondelete="CASCADE"),
        primary_key=True,
    )
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Stored as a JSON list; queried as-is, never filtered per-scope in SQL
    scopes = Column(Text, nullable=False)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="google_token")

    @property
    def scopes_list(self) -> list[str]:
        return json.loads(self.scopes)

    def is_expired(self, buffer_seconds: int = 60) -> bool:
        """True when the access token expires within `buffer_seconds` from now.

        The 60-second buffer prevents using a token that will expire mid-request.
        """
        delta = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return delta < buffer_seconds
