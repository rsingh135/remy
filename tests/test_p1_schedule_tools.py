"""
Tests for schedule-related tools: query_schedule, list_reminders, cancel_reminder.

query_schedule contract:
  - Reminders appear for the date their execution_timestamp falls on, not the
    date they were created. A reminder set last week for tomorrow should show
    up in tomorrow's schedule, not today's.
  - Non-reminder events (fitness_log, task) still surface by creation date.

list_reminders contract:
  - Returns only reminders whose execution_timestamp is in the future.
  - Past reminders and non-reminder events are excluded.
  - Each entry exposes task_id, message, and eta.

cancel_reminder contract:
  - Removes the Event row from the database.
  - Calls Celery revoke with the correct task_id so the queued task won't fire.
  - Returns an error dict when the task_id doesn't exist.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.database import AsyncSessionLocal
from app.models.event import Event
from app.models.user import User
from app.services.tools import execute_tool
from app.tasks.celery_app import celery_app

_PHONE = "+15550001001"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reminder_payload(message: str, eta: datetime, task_id: str = "task-abc-123") -> dict:
    return {
        "message": message,
        "execution_timestamp": eta.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_id": task_id,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def schedule_user():
    async with AsyncSessionLocal() as db:
        user = User(phone_number=_PHONE, name="Scheduler", onboarding_step=6, timezone="UTC")
        db.add(user)
        await db.commit()
        await db.refresh(user)

    yield

    async with AsyncSessionLocal() as db:
        await db.execute(User.__table__.delete().where(User.phone_number == _PHONE))
        await db.commit()


# ---------------------------------------------------------------------------
# query_schedule — reminder filtering by execution_timestamp
# ---------------------------------------------------------------------------

async def test_query_schedule_finds_reminder_by_execution_date(schedule_user):
    """A reminder whose execution_timestamp falls on the target date is returned
    regardless of when the reminder was created."""
    target_date = date(2030, 6, 15)
    eta = datetime(2030, 6, 15, 14, 0, 0, tzinfo=timezone.utc)

    async with AsyncSessionLocal() as db:
        db.add(Event(user_phone=_PHONE, event_type="reminder", payload=_reminder_payload("study session", eta)))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("query_schedule", {"date_str": target_date.isoformat()}, user, db)

    assert result["date"] == "2030-06-15"
    reminder_events = [e for e in result["events"] if e["event_type"] == "reminder"]
    assert len(reminder_events) == 1
    assert reminder_events[0]["payload"]["message"] == "study session"


async def test_query_schedule_excludes_reminder_from_other_dates(schedule_user):
    """A reminder scheduled for date A must not appear in the schedule for date B."""
    eta_tomorrow = datetime(2030, 6, 16, 9, 0, 0, tzinfo=timezone.utc)

    async with AsyncSessionLocal() as db:
        db.add(Event(user_phone=_PHONE, event_type="reminder", payload=_reminder_payload("wrong day", eta_tomorrow)))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("query_schedule", {"date_str": "2030-06-15"}, user, db)

    reminder_events = [e for e in result["events"] if e["event_type"] == "reminder"]
    assert all(e["payload"]["message"] != "wrong day" for e in reminder_events)


async def test_query_schedule_includes_non_reminder_event_by_creation_date(schedule_user):
    """Fitness-log and task events still appear on the date they were logged.
    Uses UTC date to match the timezone used by PostgreSQL's now()."""
    today = datetime.now(timezone.utc).date().isoformat()

    async with AsyncSessionLocal() as db:
        db.add(Event(
            user_phone=_PHONE,
            event_type="fitness_log",
            payload={"water_liters": 2.5},
        ))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("query_schedule", {"date_str": today}, user, db)

    fitness_events = [e for e in result["events"] if e["event_type"] == "fitness_log"]
    assert len(fitness_events) >= 1


async def test_query_schedule_returns_error_on_bad_date_format(schedule_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("query_schedule", {"date_str": "not-a-date"}, user, db)

    assert "error" in result


# ---------------------------------------------------------------------------
# list_reminders — future-only filter
# ---------------------------------------------------------------------------

async def test_list_reminders_returns_future_reminders(schedule_user):
    future_eta = datetime.now(timezone.utc) + timedelta(hours=3)

    async with AsyncSessionLocal() as db:
        db.add(Event(
            user_phone=_PHONE,
            event_type="reminder",
            payload=_reminder_payload("gym time", future_eta, task_id="future-task-1"),
        ))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("list_reminders", {}, user, db)

    messages = [r["message"] for r in result["pending_reminders"]]
    assert "gym time" in messages
    assert result["count"] >= 1


async def test_list_reminders_excludes_past_reminders(schedule_user):
    """Reminders with an execution_timestamp in the past must not surface."""
    past_eta = datetime.now(timezone.utc) - timedelta(hours=2)

    async with AsyncSessionLocal() as db:
        db.add(Event(
            user_phone=_PHONE,
            event_type="reminder",
            payload=_reminder_payload("already fired", past_eta, task_id="past-task-1"),
        ))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("list_reminders", {}, user, db)

    messages = [r["message"] for r in result["pending_reminders"]]
    assert "already fired" not in messages


async def test_list_reminders_returns_empty_when_no_pending(schedule_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("list_reminders", {}, user, db)

    assert result["pending_reminders"] == []
    assert result["count"] == 0


async def test_list_reminders_exposes_required_fields(schedule_user):
    """Each reminder entry must include task_id, message, and eta."""
    eta = datetime.now(timezone.utc) + timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        db.add(Event(
            user_phone=_PHONE,
            event_type="reminder",
            payload=_reminder_payload("check email", eta, task_id="field-test-task"),
        ))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("list_reminders", {}, user, db)

    entry = next(r for r in result["pending_reminders"] if r["message"] == "check email")
    assert entry["task_id"] == "field-test-task"
    assert entry["eta"] is not None


# ---------------------------------------------------------------------------
# cancel_reminder — DB deletion + Celery revoke
# ---------------------------------------------------------------------------

async def test_cancel_reminder_removes_event_from_db(schedule_user, mocker):
    """Cancellation must delete the Event row so the reminder no longer appears."""
    mocker.patch.object(celery_app.control, "revoke")

    task_id = "cancel-test-task-99"
    eta = datetime.now(timezone.utc) + timedelta(hours=5)

    async with AsyncSessionLocal() as db:
        db.add(Event(
            user_phone=_PHONE,
            event_type="reminder",
            payload=_reminder_payload("cancel me", eta, task_id=task_id),
        ))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("cancel_reminder", {"task_id": task_id}, user, db)

    assert result["status"] == "cancelled"

    async with AsyncSessionLocal() as db:
        remaining = (await db.execute(
            select(Event)
            .where(Event.user_phone == _PHONE)
            .where(Event.event_type == "reminder")
            .where(Event.payload["task_id"].astext == task_id)
        )).scalar_one_or_none()
    assert remaining is None


async def test_cancel_reminder_calls_celery_revoke_with_task_id(schedule_user, mocker):
    """The Celery task must be revoked so the queued send_reminder won't fire."""
    mock_revoke = mocker.patch.object(celery_app.control, "revoke")

    task_id = "revoke-check-task-77"
    eta = datetime.now(timezone.utc) + timedelta(hours=4)

    async with AsyncSessionLocal() as db:
        db.add(Event(
            user_phone=_PHONE,
            event_type="reminder",
            payload=_reminder_payload("revoke this", eta, task_id=task_id),
        ))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        await execute_tool("cancel_reminder", {"task_id": task_id}, user, db)

    mock_revoke.assert_called_once_with(task_id, terminate=True)


async def test_cancel_reminder_returns_error_for_unknown_task_id(schedule_user, mocker):
    mocker.patch.object(celery_app.control, "revoke")

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("cancel_reminder", {"task_id": "does-not-exist"}, user, db)

    assert "error" in result
