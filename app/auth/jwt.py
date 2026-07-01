import logging
import random
import string
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

_bearer = HTTPBearer()


def _otp_redis_key(contact_id: str) -> str:
    return f"otp:{contact_id}"


def generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


def store_otp(contact_id: str, otp: str) -> None:
    import redis as redis_lib
    s = get_settings()
    r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)
    r.setex(_otp_redis_key(contact_id), s.OTP_TTL_SECONDS, otp)


def verify_otp(contact_id: str, otp: str) -> bool:
    import redis as redis_lib
    s = get_settings()
    r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)
    key = _otp_redis_key(contact_id)
    stored = r.get(key)
    if stored and stored == otp:
        r.delete(key)
        return True
    return False


def create_token(contact_id: str) -> str:
    s = get_settings()
    payload = {
        "sub": contact_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=s.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, s.JWT_SECRET, algorithm=s.JWT_ALGORITHM)


def decode_token(token: str) -> str:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.JWT_SECRET, algorithms=[s.JWT_ALGORITHM])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    contact_id = decode_token(credentials.credentials)
    result = await db.execute(select(User).where(User.contact_id == contact_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
