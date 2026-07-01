"""
Tests for P2 features: task management, fitness summaries, profile updates,
and Google Calendar read.

list_tasks contract:
  - Returns only non-done tasks by default.
  - Accepts an explicit status filter to narrow results.
  - Returns required fields: event_id, description, status, priority, deadline.
  - Returns an empty list when no tasks exist.

update_task contract:
  - Persists status changes to the JSONB payload.
  - Persists priority changes to the JSONB payload.
  - Returns an error for an unknown event_id.
  - Validates the new values (bad status/priority rejected by the schema).

query_fitness_summary contract:
  - Aggregates protein, water, and workout counts correctly.
  - Respects the period filter (today / week / month).
  - Returns zeroes cleanly when no logs exist for the period.
  - Rejects unknown periods with an error.

update_profile contract:
  - Persists persona_style, core_goal, and objective changes to the users row.
  - Rejects invalid values for persona_style and objective.
  - Rejects unknown field names.

list_calendar_events contract (unit-level, Google API mocked):
  - Calls the Calendar API with the correct calendarId, timeMin, timeMax, and ordering.
  - Returns the expected fields: title, start, end, description, event_id.
  - Handles an empty events list without error.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.database import AsyncSessionLocal
from app.models.event import Event
from app.models.user import User
from app.services.tools import execute_tool

_PHONE = "+15550002001"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def p2_user():
    async with AsyncSessionLocal() as db:
        user = User(
            phone_number=_PHONE,
            name="P2Tester",
            onboarding_step=6,
            timezone="UTC",
            persona_style="chill_coach",
            objective="habit_architect",
            core_goal="run every day",
        )
        db.add(user)
        await db.commit()

    yield

    async with AsyncSessionLocal() as db:
        await db.execute(User.__table__.delete().where(User.phone_number == _PHONE))
        await db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_payload(description: str, status: str = "pending", priority: str = "medium") -> dict:
    return {"description": description, "status": status, "priority": priority, "deadline": None}


def _fitness_payload(**kwargs) -> dict:
    return {
        "protein_grams": kwargs.get("protein_grams"),
        "water_liters": kwargs.get("water_liters"),
        "workout_type": kwargs.get("workout_type"),
        "duration_minutes": kwargs.get("duration_minutes"),
        "notes": None,
    }


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------

async def test_list_tasks_returns_only_open_tasks_by_default(p2_user):
    async with AsyncSessionLocal() as db:
        db.add(Event(user_phone=_PHONE, event_type="task", payload=_task_payload("Buy groceries", status="pending")))
        db.add(Event(user_phone=_PHONE, event_type="task", payload=_task_payload("Call mom", status="done")))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("list_tasks", {}, user, db)

    descriptions = [t["description"] for t in result["tasks"]]
    assert "Buy groceries" in descriptions
    assert "Call mom" not in descriptions


async def test_list_tasks_status_filter_returns_done_tasks(p2_user):
    async with AsyncSessionLocal() as db:
        db.add(Event(user_phone=_PHONE, event_type="task", payload=_task_payload("Finished task", status="done")))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("list_tasks", {"status": "done"}, user, db)

    descriptions = [t["description"] for t in result["tasks"]]
    assert "Finished task" in descriptions


async def test_list_tasks_returns_required_fields(p2_user):
    async with AsyncSessionLocal() as db:
        db.add(Event(user_phone=_PHONE, event_type="task", payload=_task_payload("Write tests", priority="high")))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("list_tasks", {}, user, db)

    entry = next(t for t in result["tasks"] if t["description"] == "Write tests")
    assert entry["event_id"] is not None
    assert entry["status"] == "pending"
    assert entry["priority"] == "high"
    assert "deadline" in entry


async def test_list_tasks_returns_empty_when_no_open_tasks(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("list_tasks", {}, user, db)

    assert result["tasks"] == []
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------

async def test_update_task_persists_status_change(p2_user):
    async with AsyncSessionLocal() as db:
        event = Event(user_phone=_PHONE, event_type="task", payload=_task_payload("Update me"))
        db.add(event)
        await db.commit()
        event_id = event.id

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("update_task", {"event_id": event_id, "status": "done"}, user, db)

    assert result["status"] == "updated"

    async with AsyncSessionLocal() as db:
        event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one()
    assert event.payload["status"] == "done"


async def test_update_task_persists_priority_change(p2_user):
    async with AsyncSessionLocal() as db:
        event = Event(user_phone=_PHONE, event_type="task", payload=_task_payload("Prioritise me"))
        db.add(event)
        await db.commit()
        event_id = event.id

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        await execute_tool("update_task", {"event_id": event_id, "priority": "high"}, user, db)

    async with AsyncSessionLocal() as db:
        event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one()
    assert event.payload["priority"] == "high"


async def test_update_task_returns_error_for_unknown_event_id(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("update_task", {"event_id": 999999999, "status": "done"}, user, db)

    assert "error" in result


# ---------------------------------------------------------------------------
# query_fitness_summary
# ---------------------------------------------------------------------------

async def test_fitness_summary_aggregates_protein_and_water(p2_user):
    async with AsyncSessionLocal() as db:
        db.add(Event(user_phone=_PHONE, event_type="fitness_log", payload=_fitness_payload(protein_grams=40, water_liters=1.5)))
        db.add(Event(user_phone=_PHONE, event_type="fitness_log", payload=_fitness_payload(protein_grams=60, water_liters=1.0)))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("query_fitness_summary", {"period": "week"}, user, db)

    assert result["total_protein_grams"] == 100.0
    assert result["total_water_liters"] == 2.5
    assert result["log_count"] == 2


async def test_fitness_summary_counts_workouts(p2_user):
    async with AsyncSessionLocal() as db:
        db.add(Event(user_phone=_PHONE, event_type="fitness_log", payload=_fitness_payload(workout_type="run", protein_grams=30)))
        db.add(Event(user_phone=_PHONE, event_type="fitness_log", payload=_fitness_payload(workout_type="lift", protein_grams=50)))
        db.add(Event(user_phone=_PHONE, event_type="fitness_log", payload=_fitness_payload(water_liters=2.0)))
        await db.commit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("query_fitness_summary", {"period": "week"}, user, db)

    assert result["workout_count"] == 2
    assert "run" in result["workouts"]
    assert "lift" in result["workouts"]


async def test_fitness_summary_returns_zeroes_when_no_logs(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("query_fitness_summary", {"period": "week"}, user, db)

    assert result["total_protein_grams"] == 0.0
    assert result["total_water_liters"] == 0.0
    assert result["workout_count"] == 0
    assert result["log_count"] == 0


async def test_fitness_summary_rejects_unknown_period(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("query_fitness_summary", {"period": "quarter"}, user, db)

    assert "error" in result


# ---------------------------------------------------------------------------
# update_profile
# ---------------------------------------------------------------------------

async def test_update_profile_changes_persona_style(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("update_profile", {"field": "persona_style", "value": "drill_sergeant"}, user, db)

    assert result["status"] == "updated"

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
    assert user.persona_style == "drill_sergeant"


async def test_update_profile_changes_core_goal(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        await execute_tool("update_profile", {"field": "core_goal", "value": "sleep 8 hours every night"}, user, db)

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
    assert user.core_goal == "sleep 8 hours every night"


async def test_update_profile_changes_objective(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        await execute_tool("update_profile", {"field": "objective", "value": "study_buddy"}, user, db)

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
    assert user.objective == "study_buddy"


async def test_update_profile_rejects_invalid_persona(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("update_profile", {"field": "persona_style", "value": "friendly_robot"}, user, db)

    assert "error" in result


async def test_update_profile_rejects_invalid_objective(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("update_profile", {"field": "objective", "value": "grind_mode"}, user, db)

    assert "error" in result


async def test_update_profile_rejects_unknown_field(p2_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("update_profile", {"field": "phone_number", "value": "+10000000000"}, user, db)

    assert "error" in result


# ---------------------------------------------------------------------------
# list_calendar_events (Google API mocked — no real OAuth needed)
# ---------------------------------------------------------------------------

async def test_list_calendar_events_calls_api_with_correct_params(p2_user):
    """list_calendar_events must pass calendarId=primary, timeMin, timeMax,
    singleEvents=True, and orderBy=startTime to the Google Calendar API."""
    time_min = "2030-06-30T00:00:00Z"
    time_max = "2030-06-30T23:59:59Z"

    mock_service = MagicMock()
    mock_events_list = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    with patch("app.services.google_tools.get_google_service", new=AsyncMock(return_value=mock_service)):
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
            result = await execute_tool(
                "list_calendar_events",
                {"time_min_iso": time_min, "time_max_iso": time_max},
                user,
                db,
            )

    list_call_kwargs = mock_service.events.return_value.list.call_args.kwargs
    assert list_call_kwargs["calendarId"] == "primary"
    assert list_call_kwargs["timeMin"] == time_min
    assert list_call_kwargs["timeMax"] == time_max
    assert list_call_kwargs["singleEvents"] is True
    assert list_call_kwargs["orderBy"] == "startTime"


async def test_list_calendar_events_returns_required_fields(p2_user):
    """Each returned event must expose title, start, end, description, event_id."""
    fake_events = [
        {
            "id": "evt-abc-123",
            "summary": "Team standup",
            "start": {"dateTime": "2030-06-30T09:00:00-05:00"},
            "end": {"dateTime": "2030-06-30T09:30:00-05:00"},
            "description": "Daily sync",
        }
    ]

    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {"items": fake_events}

    with patch("app.services.google_tools.get_google_service", new=AsyncMock(return_value=mock_service)):
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
            result = await execute_tool(
                "list_calendar_events",
                {"time_min_iso": "2030-06-30T00:00:00Z", "time_max_iso": "2030-06-30T23:59:59Z"},
                user,
                db,
            )

    assert result["count"] == 1
    evt = result["events"][0]
    assert evt["title"] == "Team standup"
    assert evt["start"] == "2030-06-30T09:00:00-05:00"
    assert evt["end"] == "2030-06-30T09:30:00-05:00"
    assert evt["description"] == "Daily sync"
    assert evt["event_id"] == "evt-abc-123"


async def test_list_calendar_events_returns_empty_list_when_no_events(p2_user):
    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}

    with patch("app.services.google_tools.get_google_service", new=AsyncMock(return_value=mock_service)):
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
            result = await execute_tool(
                "list_calendar_events",
                {"time_min_iso": "2030-06-30T00:00:00Z", "time_max_iso": "2030-06-30T23:59:59Z"},
                user,
                db,
            )

    assert result["events"] == []
    assert result["count"] == 0
