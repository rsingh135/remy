import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import redis as redis_lib
from sqlalchemy import select

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.models.user import User
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_NIGHTLY_MESSAGES: dict[str, str] = {
    "study_buddy": "Hey {name} — how'd studying go today? Making progress on {goal}?",
    "habit_architect": "Hey {name} — habits on point today? Did you work toward {goal}?",
    "idea_vault": "Hey {name} — capture anything worth keeping today? Any progress on {goal}?",
    "hybrid": "Hey {name} — productive one? What moved the needle on {goal}?",
}
_NIGHTLY_MESSAGE_DEFAULT = "Hey {name} — did you hit your core goal today? What's the plan for tomorrow?"


def _build_nightly_message(name: str, objective: str | None, core_goal: str | None) -> str:
    template = _NIGHTLY_MESSAGES.get(objective or "", _NIGHTLY_MESSAGE_DEFAULT)
    goal_snippet = (core_goal or "your goal")[:40].rstrip()
    return template.format(name=name, goal=goal_snippet)


async def _get_users_for_nightly(now_utc: datetime) -> list[User]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(
                User.onboarding_step >= 6,
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
                logger.warning("Invalid timezone for user %s: %s", user.contact_id, user.timezone)

        return matching


@celery_app.task(name="app.tasks.nightly.dispatch_nightly_commits")
def dispatch_nightly_commits() -> None:
    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    users = asyncio.run(_get_users_for_nightly(now_utc))
    for user in users:
        send_nightly_commit.delay(
            user.contact_id,
            user.name or "friend",
            user.objective,
            user.core_goal,
        )
        logger.info("Dispatched nightly commit for %s", user.contact_id)


@celery_app.task(name="app.tasks.nightly.send_nightly_commit")
def send_nightly_commit(
    phone_number: str,
    name: str,
    objective: str | None = None,
    core_goal: str | None = None,
) -> None:
    s = get_settings()
    r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)

    today = date.today().isoformat()
    redis_key = f"nightly_sent:{phone_number}:{today}"

    if r.exists(redis_key):
        logger.info("Nightly already sent today for %s, skipping", phone_number)
        return

    message = _build_nightly_message(name, objective, core_goal)
    s = get_settings()
    try:
        if s.PHOTON_ENABLED:
            from app.services.photon_sender import send_via_photon
            asyncio.run(send_via_photon(phone_number, message))
        else:
            from app.services.sms_sender import _send_sms_boto3
            _send_sms_boto3(phone_number, message)
    except Exception as exc:
        from app.services.alerting import send_admin_alert
        send_admin_alert(
            subject="[Remy] Nightly check-in delivery failed",
            message=f"send_nightly_commit failed for user {phone_number}. Error: {exc}",
        )
        raise

    seconds_until_midnight = _seconds_until_midnight_utc()
    r.setex(redis_key, seconds_until_midnight + 3600, "1")

    logger.info("Nightly commit sent to %s", phone_number)


@celery_app.task(name="app.tasks.nightly.schedule_first_nightly")
def schedule_first_nightly(phone_number: str) -> None:
    logger.info("User %s onboarded, nightly commits enabled via Beat schedule", phone_number)


_FOLLOWUP_MESSAGES: dict[str, str] = {
    "study_buddy": "hey {name}, day 1 check-in — any studying happen today?",
    "habit_architect": "hey {name}, how'd day 1 go? any habits locked in?",
    "idea_vault": "hey {name}, capture anything worth keeping on day 1?",
    "hybrid": "hey {name}, day 1 in the books — how'd it go?",
}


@celery_app.task(name="app.tasks.nightly.send_onboarding_followup")
def send_onboarding_followup(phone_number: str, name: str, objective: str | None = None) -> None:
    template = _FOLLOWUP_MESSAGES.get(objective or "", "hey {name}, how's day 1 going so far?")
    message = template.format(name=name)

    s = get_settings()
    if s.PHOTON_ENABLED:
        from app.services.photon_sender import send_via_photon
        asyncio.run(send_via_photon(phone_number, message))
    else:
        from app.services.sms_sender import _send_sms_boto3
        _send_sms_boto3(phone_number, message)

    logger.info("Onboarding follow-up sent to %s", phone_number)


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(tz=ZoneInfo("UTC"))
    midnight = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=ZoneInfo("UTC"))
    return max(0, int((midnight - now).total_seconds()))
