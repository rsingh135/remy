import json
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime as SADateTime
from sqlalchemy import cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.vector_store import store_memory
from app.models.event import Event
from app.models.memory import Memory
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
    from app.services.google_service import GoogleTokenExpiredError

    try:
        match name:
            case "add_reminder":
                return await _tool_add_reminder(inputs, user, db)
            case "log_event":
                return await _tool_log_event(inputs, user, db)
            case "query_schedule":
                return await _tool_query_schedule(inputs, user, db)
            case "list_reminders":
                return await _tool_list_reminders(user, db)
            case "cancel_reminder":
                return await _tool_cancel_reminder(inputs, user, db)
            case "store_memory":
                return await _tool_store_memory(inputs, user, db)
            case "recall_memories":
                return await _tool_recall_memories(user, db)
            case "list_tasks":
                return await _tool_list_tasks(inputs, user, db)
            case "update_task":
                return await _tool_update_task(inputs, user, db)
            case "query_fitness_summary":
                return await _tool_query_fitness_summary(inputs, user, db)
            case "update_profile":
                return await _tool_update_profile(inputs, user, db)
            case "get_google_auth_link":
                return _tool_get_google_auth_link(user)
            case "add_calendar_event":
                return await _tool_add_calendar_event(inputs, user, db)
            case "list_calendar_events":
                return await _tool_list_calendar_events(inputs, user, db)
            case "send_gmail":
                return await _tool_send_gmail(inputs, user, db)
            case "read_gmail":
                return await _tool_read_gmail(inputs, user, db)
            case _:
                return {"error": f"Unknown tool: {name}"}
    except GoogleTokenExpiredError:
        logger.warning("Google token expired for %s during tool %s", user.phone_number, name)
        auth_info = _tool_get_google_auth_link(user)
        return {
            "error": "Google authorization expired.",
            "action": (
                f"Tell the user their Google connection has expired and they need to reconnect. "
                f"Send them this link: {auth_info['auth_url']}"
            ),
        }
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

    # Prevent duplicate Celery tasks if Claude calls add_reminder twice in one turn
    existing = await db.execute(
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.event_type == "reminder")
        .where(Event.payload["execution_timestamp"].astext == eta_dt.isoformat())
        .where(Event.payload["message"].astext == payload.message)
    )
    if existing.scalar_one_or_none() is not None:
        logger.warning("Duplicate add_reminder call suppressed for %s at %s", user.phone_number, eta_dt)
        return {"status": "already_scheduled", "eta": eta_dt.isoformat()}

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

    # Non-reminder events: filtered by creation timestamp
    result_other = await db.execute(
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.event_type != "reminder")
        .where(Event.timestamp >= start)
        .where(Event.timestamp <= end)
        .order_by(Event.timestamp)
    )

    # Reminder events: filtered by scheduled execution_timestamp so "what's today?" surfaces
    # reminders *for* today regardless of when they were created.
    execution_ts = cast(Event.payload["execution_timestamp"].astext, SADateTime(timezone=True))
    result_reminders = await db.execute(
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.event_type == "reminder")
        .where(execution_ts >= start)
        .where(execution_ts <= end)
        .order_by(execution_ts)
    )

    events = list(result_other.scalars().all()) + list(result_reminders.scalars().all())
    events.sort(key=lambda e: e.timestamp)

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


async def _tool_list_reminders(user: User, db: AsyncSession) -> dict:
    now_utc = datetime.now(tz=timezone.utc)
    execution_ts = cast(Event.payload["execution_timestamp"].astext, SADateTime(timezone=True))
    result = await db.execute(
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.event_type == "reminder")
        .where(execution_ts > now_utc)
        .order_by(execution_ts)
    )
    events = result.scalars().all()
    return {
        "pending_reminders": [
            {
                "task_id": e.payload.get("task_id"),
                "message": e.payload.get("message"),
                "eta": e.payload.get("execution_timestamp"),
            }
            for e in events
        ],
        "count": len(events),
    }


async def _tool_cancel_reminder(inputs: dict, user: User, db: AsyncSession) -> dict:
    from app.tasks.celery_app import celery_app

    task_id = inputs["task_id"]
    result = await db.execute(
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.event_type == "reminder")
        .where(Event.payload["task_id"].astext == task_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        return {"error": "Reminder not found"}

    celery_app.control.revoke(task_id, terminate=True)
    await db.delete(event)
    await db.commit()
    return {"status": "cancelled", "task_id": task_id}


async def _tool_recall_memories(user: User, db: AsyncSession) -> dict:
    result = await db.execute(
        select(Memory)
        .where(Memory.user_phone == user.phone_number)
        .order_by(Memory.created_at.desc())
        .limit(30)
    )
    memories = result.scalars().all()
    return {
        "memories": [
            {
                "category": m.category,
                "text": m.memory_text,
                "stored_at": m.created_at.isoformat(),
            }
            for m in memories
        ],
        "total": len(memories),
    }


async def _tool_list_tasks(inputs: dict, user: User, db: AsyncSession) -> dict:
    status_filter = inputs.get("status")
    stmt = (
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.event_type == "task")
        .order_by(Event.timestamp.desc())
    )
    if status_filter:
        stmt = stmt.where(Event.payload["status"].astext == status_filter)
    else:
        stmt = stmt.where(Event.payload["status"].astext != "done")

    result = await db.execute(stmt)
    events = result.scalars().all()
    return {
        "tasks": [
            {
                "event_id": e.id,
                "description": e.payload.get("description"),
                "status": e.payload.get("status"),
                "priority": e.payload.get("priority"),
                "deadline": e.payload.get("deadline"),
            }
            for e in events
        ],
        "count": len(events),
    }


async def _tool_update_task(inputs: dict, user: User, db: AsyncSession) -> dict:
    from sqlalchemy.orm.attributes import flag_modified

    from app.schemas.payloads import TaskPayload

    event_id = inputs["event_id"]
    result = await db.execute(
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.event_type == "task")
        .where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        return {"error": f"Task {event_id} not found"}

    updated = dict(event.payload)
    if "status" in inputs:
        updated["status"] = inputs["status"]
    if "priority" in inputs:
        updated["priority"] = inputs["priority"]

    try:
        validated = TaskPayload(**updated)
    except Exception as exc:
        return {"error": f"Invalid task data: {exc}"}

    event.payload = validated.model_dump(mode="json")
    flag_modified(event, "payload")
    await db.commit()

    return {"status": "updated", "event_id": event_id, "task": event.payload}


async def _tool_query_fitness_summary(inputs: dict, user: User, db: AsyncSession) -> dict:
    from datetime import timedelta

    period = inputs.get("period", "week")
    now = datetime.now(tz=timezone.utc)

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = now - timedelta(days=7)
    elif period == "month":
        start = now - timedelta(days=30)
    else:
        return {"error": f"Unknown period '{period}'. Use 'today', 'week', or 'month'."}

    result = await db.execute(
        select(Event)
        .where(Event.user_phone == user.phone_number)
        .where(Event.event_type == "fitness_log")
        .where(Event.timestamp >= start)
        .order_by(Event.timestamp)
    )
    events = result.scalars().all()

    total_protein = sum(e.payload.get("protein_grams") or 0 for e in events)
    total_water = sum(e.payload.get("water_liters") or 0 for e in events)
    workouts = [e.payload["workout_type"] for e in events if e.payload.get("workout_type")]

    return {
        "period": period,
        "log_count": len(events),
        "total_protein_grams": round(total_protein, 1),
        "total_water_liters": round(total_water, 2),
        "workouts": workouts,
        "workout_count": len(workouts),
    }


_VALID_PERSONAS = {"chill_coach", "no_bs_peer", "drill_sergeant"}
_VALID_OBJECTIVES = {"study_buddy", "habit_architect", "idea_vault", "hybrid"}


async def _tool_update_profile(inputs: dict, user: User, db: AsyncSession) -> dict:
    field = inputs["field"]
    value = inputs["value"]

    if field == "persona_style":
        if value not in _VALID_PERSONAS:
            return {"error": f"Invalid persona_style '{value}'. Choose from: {sorted(_VALID_PERSONAS)}"}
        user.persona_style = value
    elif field == "core_goal":
        user.core_goal = value[:500]
    elif field == "objective":
        if value not in _VALID_OBJECTIVES:
            return {"error": f"Invalid objective '{value}'. Choose from: {sorted(_VALID_OBJECTIVES)}"}
        user.objective = value
    elif field == "gmail_read_enabled":
        user.gmail_read_enabled = value.lower() in ("true", "1", "yes", "on")
    else:
        return {"error": f"Cannot update '{field}'. Allowed fields: persona_style, core_goal, objective, gmail_read_enabled"}

    await db.commit()
    return {"status": "updated", "field": field, "value": value}


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


async def _tool_list_calendar_events(
    inputs: dict,
    user: User,
    db: AsyncSession,
) -> dict:
    from app.services.google_tools import list_calendar_events
    return await list_calendar_events(
        user_phone=user.phone_number,
        time_min_iso=inputs["time_min_iso"],
        time_max_iso=inputs["time_max_iso"],
        db=db,
        max_results=inputs.get("max_results", 10),
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


async def _tool_read_gmail(
    inputs: dict,
    user: User,
    db: AsyncSession,
) -> dict:
    if not user.gmail_read_enabled:
        return {
            "error": "Gmail reading is not enabled for this user.",
            "hint": (
                "Tell the user: 'Gmail reading is off by default. "
                "Say \"enable Gmail reading\" and I'll turn it on for you.'"
            ),
        }
    from app.services.google_tools import read_gmail_messages
    max_results = int(inputs.get("max_results") or 5)
    return await read_gmail_messages(
        user_phone=user.phone_number,
        db=db,
        max_results=max_results,
    )
