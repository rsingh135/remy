import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import redis as redis_lib
from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.sms_sender import _send_sms_boto3
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_NIGHTLY_MESSAGE = (
    "Hey {name} — did you hit your core goal today? "
    "What's the plan for tomorrow?"
)


async def _get_users_for_nightly(now_utc: datetime) -> list[User]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(
                User.onboarding_step >= 5,
                User.is_paused.is_(False),
                User.timezone.is_not(None),
            )
        )
        all_users = result.scalars().all()

        matching = []
        for user in all_users:
            try:
                local_now = now_utc.astimezone(ZoneInfo(user.timezone))
                if local_now.hour == 21:
                    matching.append(user)
            except (ZoneInfoNotFoundError, KeyError):
                logger.warning("Invalid timezone for user %s: %s", user.phone_number, user.timezone)

        return matching


@celery_app.task(name="app.tasks.nightly.dispatch_nightly_commits")
def dispatch_nightly_commits() -> None:
    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    users = asyncio.run(_get_users_for_nightly(now_utc))
    for user in users:
        send_nightly_commit.delay(user.phone_number, user.name or "friend")
        logger.info("Dispatched nightly commit for %s", user.phone_number)


@celery_app.task(name="app.tasks.nightly.send_nightly_commit")
def send_nightly_commit(phone_number: str, name: str) -> None:
    s = get_settings()
    r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)

    today = date.today().isoformat()
    redis_key = f"nightly_sent:{phone_number}:{today}"

    if r.exists(redis_key):
        logger.info("Nightly already sent today for %s, skipping", phone_number)
        return

    message = _NIGHTLY_MESSAGE.format(name=name)
    _send_sms_boto3(phone_number, message)

    seconds_until_midnight = _seconds_until_midnight_utc()
    r.setex(redis_key, seconds_until_midnight + 3600, "1")

    logger.info("Nightly commit sent to %s", phone_number)


@celery_app.task(name="app.tasks.nightly.schedule_first_nightly")
def schedule_first_nightly(phone_number: str) -> None:
    logger.info("User %s onboarded, nightly commits enabled via Beat schedule", phone_number)


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(tz=ZoneInfo("UTC"))
    midnight = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=ZoneInfo("UTC"))
    return max(0, int((midnight - now).total_seconds()))
