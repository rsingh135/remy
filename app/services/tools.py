import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.vector_store import store_memory
from app.models.event import Event
from app.models.user import User
from app.schemas.payloads import PAYLOAD_SCHEMA_MAP, ReminderPayload

logger = logging.getLogger(__name__)

_AFFIRMATIVE_KEYWORDS = {
    "yes", "yeah", "yep", "yup", "done", "hit it", "completed", "finished",
    "absolutely", "definitely", "of course", "sure", "killed it", "crushed it",
    "nailed it", "achieved", "accomplished",
}


async def execute_tool(
    name: str,
    inputs: dict,
    user: User,
    db: AsyncSession,
) -> dict:
    try:
        match name:
            case "add_reminder":
                return await _tool_add_reminder(inputs, user, db)
            case "log_event":
                return await _tool_log_event(inputs, user, db)
            case "query_schedule":
                return await _tool_query_schedule(inputs, user, db)
            case "store_memory":
                return await _tool_store_memory(inputs, user, db)
            case "get_google_auth_link":
                return _tool_get_google_auth_link(user)
            case "add_calendar_event":
                return await _tool_add_calendar_event(inputs, user, db)
            case "send_gmail":
                return await _tool_send_gmail(inputs, user, db)
            case _:
                return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return {"error": str(e)}


async def _tool_add_reminder(inputs: dict, user: User, db: AsyncSession) -> dict:
    from app.tasks.reminders import send_reminder

    eta_dt = datetime.fromisoformat(inputs["time_str"].replace("Z", "+00:00"))
    if eta_dt.tzinfo is None:
        eta_dt = eta_dt.replace(tzinfo=ZoneInfo("UTC"))

    payload = ReminderPayload(
        message=inputs["message"][:160],
        execution_timestamp=eta_dt,
    )

    task = send_reminder.apply_async(
        args=[user.phone_number, payload.message],
        eta=eta_dt,
    )

    payload_dict = payload.model_dump(mode="json")
    payload_dict["task_id"] = task.id

    event = Event(
        user_phone=user.phone_number,
        event_type="reminder",
        payload=payload_dict,
    )
    db.add(event)
    await db.commit()

    return {"status": "scheduled", "task_id": task.id, "eta": eta_dt.isoformat()}


async def _tool_log_event(inputs: dict, user: User, db: AsyncSession) -> dict:
    event_type = inputs["event_type"]
    data = inputs["data"]

    schema_cls = PAYLOAD_SCHEMA_MAP.get(event_type)
    if schema_cls is None:
        return {"error": f"Unknown event_type: {event_type}"}

    validated = schema_cls(**data)

    event = Event(
        user_phone=user.phone_number,
        event_type=event_type,
        payload=validated.model_dump(mode="json"),
    )
    db.add(event)
    await db.commit()

    return {"status": "logged", "event_type": event_type}


async def _tool_query_schedule(inputs: dict, user: User, db: AsyncSession) -> dict:
    date_str = inputs["date_str"]
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        return {"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD."}

    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=ZoneInfo("UTC"))
    end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=ZoneInfo("UTC"))

    result = await db.execute(
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.timestamp >= start)
        .where(Event.timestamp <= end)
        .order_by(Event.timestamp)
    )
    events = result.scalars().all()

    return {
        "date": date_str,
        "events": [
            {
                "event_type": e.event_type,
                "payload": e.payload,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in events
        ],
    }


async def _tool_store_memory(inputs: dict, user: User, db: AsyncSession) -> dict:
    category = inputs["category"]
    memory_text = inputs["memory_text"]

    valid_categories = {"academics", "fitness", "ideas", "general"}
    if category not in valid_categories:
        return {"error": f"Invalid category: {category}. Must be one of {valid_categories}"}

    await store_memory(user.phone_number, category, memory_text, db)
    return {"status": "stored", "category": category}


def is_affirmative(message: str) -> bool:
    lower = message.lower().strip()
    return any(keyword in lower for keyword in _AFFIRMATIVE_KEYWORDS)


# ---------------------------------------------------------------------------
# Google integration tool handlers
# ---------------------------------------------------------------------------

def _tool_get_google_auth_link(user: User) -> dict:
    """
    Build a signed-looking auth URL and return it for inclusion in the SMS reply.

    No DB or network call needed — the URL encodes the phone number as a query
    param; the OAuth route validates it against the users table at click time.
    The state CSRF nonce is generated there, not here.
    """
    from app.config import get_settings
    s = get_settings()
    import urllib.parse
    link = f"{s.BASE_URL}/sms/auth/google?phone={urllib.parse.quote(user.phone_number)}"
    return {
        "auth_url": link,
        "instruction": (
            f"Tell the user: 'Tap this link to connect your Google account: {link}' "
            "and that they can close the browser tab once connected."
        ),
    }


async def _tool_add_calendar_event(
    inputs: dict,
    user: User,
    db: AsyncSession,
) -> dict:
    from app.services.google_tools import add_calendar_event
    return await add_calendar_event(
        user_phone=user.phone_number,
        summary=inputs["summary"],
        start_time_iso=inputs["start_time_iso"],
        end_time_iso=inputs["end_time_iso"],
        db=db,
        description=inputs.get("description"),
    )


async def _tool_send_gmail(
    inputs: dict,
    user: User,
    db: AsyncSession,
) -> dict:
    from app.services.google_tools import send_gmail_message
    return await send_gmail_message(
        user_phone=user.phone_number,
        to_email=inputs["to_email"],
        subject=inputs["subject"],
        body_text=inputs["body_text"],
        db=db,
    )
