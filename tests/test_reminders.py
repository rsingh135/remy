"""
Reminder integration tests.

Tests the full scheduling path: tool execution → Event written to DB → Celery task enqueued.
Real RDS is used. Celery broker connection is mocked so no real broker is required.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.database import AsyncSessionLocal
from app.models.event import Event
from app.models.user import User
from app.services.tools import execute_tool


_TEST_PHONE = "+15550000042"


@pytest.fixture
async def reminder_user():
    """Create a minimal onboarded user for reminder tests, then clean up."""
    async with AsyncSessionLocal() as db:
        user = User(
            phone_number=_TEST_PHONE,
            name="Tester",
            onboarding_step=6,
            streak_count=0,
            timezone="America/Chicago",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    yield user

    async with AsyncSessionLocal() as db:
        await db.execute(User.__table__.delete().where(User.phone_number == _TEST_PHONE))
        await db.commit()


async def test_reminder_creates_event_in_db(reminder_user, mocker):
    """add_reminder tool writes an Event row to DB and returns a scheduled status."""
    mock_apply = mocker.patch("app.tasks.reminders.send_reminder.apply_async")
    mock_apply.return_value.id = "test-task-id-001"

    eta = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0)

    async with AsyncSessionLocal() as db:
        # Re-fetch user in this session
        from sqlalchemy import select
        user = (await db.execute(select(User).where(User.phone_number == _TEST_PHONE))).scalar_one()

        result = await execute_tool(
            "add_reminder",
            {"time_str": eta.isoformat(), "message": "stretch your back"},
            user,
            db,
        )

    assert result["status"] == "scheduled"
    assert result["task_id"] == "test-task-id-001"
    assert "eta" in result

    async with AsyncSessionLocal() as db:
        events = (await db.execute(
            select(Event).where(
                Event.user_phone == _TEST_PHONE,
                Event.event_type == "reminder",
            )
        )).scalars().all()

    assert len(events) == 1
    assert events[0].payload["message"] == "stretch your back"
    assert events[0].payload["task_id"] == "test-task-id-001"


async def test_reminder_apply_async_called_with_correct_eta(reminder_user, mocker):
    """Celery apply_async is called with the correct eta and phone number."""
    mock_apply = mocker.patch("app.tasks.reminders.send_reminder.apply_async")
    mock_apply.return_value.id = "test-task-id-002"

    eta = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _TEST_PHONE))).scalar_one()

        await execute_tool(
            "add_reminder",
            {"time_str": eta.isoformat(), "message": "drink water"},
            user,
            db,
        )

    mock_apply.assert_called_once()
    call_kwargs = mock_apply.call_args
    assert call_kwargs.kwargs["args"][0] == _TEST_PHONE
    assert call_kwargs.kwargs["args"][1] == "drink water"
    assert call_kwargs.kwargs["eta"].replace(microsecond=0) == eta


async def test_reminder_message_truncated_to_160(reminder_user, mocker):
    """Messages longer than 160 chars are truncated before storing."""
    mock_apply = mocker.patch("app.tasks.reminders.send_reminder.apply_async")
    mock_apply.return_value.id = "test-task-id-003"

    long_message = "x" * 200
    eta = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _TEST_PHONE))).scalar_one()

        result = await execute_tool(
            "add_reminder",
            {"time_str": eta, "message": long_message},
            user,
            db,
        )

    assert result["status"] == "scheduled"

    async with AsyncSessionLocal() as db:
        events = (await db.execute(
            select(Event).where(
                Event.user_phone == _TEST_PHONE,
                Event.event_type == "reminder",
            )
        )).scalars().all()

    assert len(events) >= 1
    assert len(events[-1].payload["message"]) <= 160
