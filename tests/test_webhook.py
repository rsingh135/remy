"""
Webhook integration tests using AWS SMS simulator numbers.

SIMULATOR_SUCCESS (+14254147755) — happy path: valid user messages
SIMULATOR_FAILURE (+14254147167) — error path: malformed / unexpected payloads

SNS signature verification is disabled via DEV_SKIP_SNS_VERIFY=true (set in conftest).
Outbound SMS is patched; no live EUM connection required.
Real Bedrock and real RDS are used.
"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

# All async tests share the session event loop so the asyncpg connection pool
# (module-level in app/database.py) is not re-bound to a new loop per test.
pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.database import AsyncSessionLocal
from app.models.user import User
from tests.conftest import (
    SIMULATOR_FAILURE,
    SIMULATOR_SUCCESS,
    build_sns_envelope,
    build_subscription_confirmation,
)


async def _post(client, phone, message):
    return await client.post("/sms/webhook", json=build_sns_envelope(phone, message))


async def _onboard(client, phone, name, objective_num, goal, persona_num, timezone):
    """Fully onboard a user: 6 messages → onboarding_step=6 (complete)."""
    await _post(client, phone, "hey")           # step 0 → intro, step→1
    await _post(client, phone, name)            # step 1 → name, step→2
    await _post(client, phone, objective_num)   # step 2 → objective, step→3
    await _post(client, phone, goal)            # step 3 → core_goal, step→4
    await _post(client, phone, persona_num)     # step 4 → persona, step→5
    await _post(client, phone, timezone)        # step 5 → timezone, step→6


# ---------------------------------------------------------------------------
# 1. Health
# ---------------------------------------------------------------------------

async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 2. SNS subscription confirmation
# ---------------------------------------------------------------------------

async def test_subscription_confirmation(client, mocker):
    mock_cm = AsyncMock()
    mock_cm.get = AsyncMock()
    mocker.patch("app.routes.sms.httpx.AsyncClient", return_value=mock_cm)

    payload = build_subscription_confirmation()
    r = await client.post("/sms/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"


# ---------------------------------------------------------------------------
# 3. SNS signature rejection (verify the guard still works when flag is off)
# ---------------------------------------------------------------------------

async def test_invalid_sns_signature_rejected(client, mocker):
    """With DEV_SKIP_SNS_VERIFY=false, bad signing URLs must be rejected."""
    mocker.patch(
        "app.services.sns_verifier.get_settings",
        return_value=type("S", (), {
            "DEV_SKIP_SNS_VERIFY": False,
            "SNS_SIGNING_CERT_URL_PREFIX": "https://sns.amazonaws.com/",
        })(),
    )
    payload = build_sns_envelope(SIMULATOR_SUCCESS, "hello")
    payload["SigningCertURL"] = "https://evil.com/cert.pem"
    r = await client.post("/sms/webhook", json=payload)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 4. Happy path — first message from new user gets greeting (SIMULATOR_SUCCESS)
# ---------------------------------------------------------------------------

async def test_new_user_receives_greeting(client, mock_send_sms, cleanup_phones):
    cleanup_phones.append(SIMULATOR_SUCCESS)

    r = await _post(client, SIMULATOR_SUCCESS, "hey")
    assert r.status_code == 200

    # step 0 fires on first text: sends intro and advances to step 1
    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(User).where(User.phone_number == SIMULATOR_SUCCESS)
        )).scalar_one_or_none()
    assert user is not None
    assert user.onboarding_step == 1
    assert user.name is None

    assert len(mock_send_sms) == 1
    assert "call you" in mock_send_sms[0]["body"].lower()
    assert mock_send_sms[0]["to"] == SIMULATOR_SUCCESS


# ---------------------------------------------------------------------------
# 5. Happy path — full onboarding flow (SIMULATOR_SUCCESS)
# ---------------------------------------------------------------------------

async def test_full_onboarding_flow(client, mock_send_sms, cleanup_phones):
    cleanup_phones.append(SIMULATOR_SUCCESS)

    steps = [
        ("hey",              "call you"),    # step 0 → intro asking for name
        ("Ranveer",          "working"),     # step 1 → name stored, asks mission
        ("2",                "habit"),       # step 2 → habit_architect, asks goal
        ("Work out daily",   "talk"),        # step 3 → goal stored, asks persona style
        ("1",                "timezone"),    # step 4 → chill_coach, asks timezone
        ("America/Chicago",  "locked"),      # step 5 → timezone stored, onboarding done
    ]

    for message, keyword in steps:
        mock_send_sms.clear()
        r = await _post(client, SIMULATOR_SUCCESS, message)
        assert r.status_code == 200, f"HTTP error for message: {message!r}"
        assert len(mock_send_sms) == 1, f"No reply sent for message: {message!r}"
        assert keyword in mock_send_sms[0]["body"].lower(), (
            f"Expected '{keyword}' for message {message!r}, got: {mock_send_sms[0]['body']!r}"
        )

    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(User).where(User.phone_number == SIMULATOR_SUCCESS)
        )).scalar_one_or_none()

    assert user.onboarding_step == 6
    assert user.name == "Ranveer"
    assert user.objective == "habit_architect"
    assert user.core_goal == "Work out daily"
    assert user.persona_style == "chill_coach"
    assert user.timezone == "America/Chicago"


# ---------------------------------------------------------------------------
# 6. Happy path — post-onboarding fitness log tool call (SIMULATOR_SUCCESS)
# ---------------------------------------------------------------------------

async def test_tool_call_log_fitness(client, mock_send_sms, cleanup_phones):
    cleanup_phones.append(SIMULATOR_SUCCESS)

    await _onboard(client, SIMULATOR_SUCCESS, "Alex", "1", "Ace my finals", "2", "America/New_York")

    mock_send_sms.clear()
    r = await _post(client, SIMULATOR_SUCCESS, "Log that I drank 2 liters of water today")
    assert r.status_code == 200
    assert len(mock_send_sms) == 1

    from app.models.event import Event
    async with AsyncSessionLocal() as db:
        events = (await db.execute(
            select(Event).where(
                Event.user_phone == SIMULATOR_SUCCESS,
                Event.event_type == "fitness_log",
            )
        )).scalars().all()
    assert len(events) >= 1
    assert events[-1].payload.get("water_liters") == 2.0


# ---------------------------------------------------------------------------
# 7. Happy path — reminder scheduling tool call (SIMULATOR_SUCCESS)
# ---------------------------------------------------------------------------

async def test_tool_call_add_reminder(client, mock_send_sms, cleanup_phones, mocker):
    cleanup_phones.append(SIMULATOR_SUCCESS)

    mock_task = mocker.patch("app.tasks.reminders.send_reminder.apply_async")
    mock_task.return_value.id = "fake-celery-id"

    await _onboard(client, SIMULATOR_SUCCESS, "Jordan", "4", "Stay consistent", "3", "US/Pacific")

    mock_send_sms.clear()
    r = await _post(client, SIMULATOR_SUCCESS, "Remind me to meditate tomorrow at 8am")
    assert r.status_code == 200
    assert len(mock_send_sms) == 1

    from app.models.event import Event
    async with AsyncSessionLocal() as db:
        events = (await db.execute(
            select(Event).where(
                Event.user_phone == SIMULATOR_SUCCESS,
                Event.event_type == "reminder",
            )
        )).scalars().all()
    assert len(events) >= 1
    assert "meditat" in events[-1].payload.get("message", "").lower()


# ---------------------------------------------------------------------------
# 8. Failure path — malformed SNS Message JSON returns 422 (SIMULATOR_FAILURE)
# ---------------------------------------------------------------------------

async def test_malformed_inner_message(client):
    payload = build_sns_envelope(SIMULATOR_FAILURE, "test")
    payload["Message"] = "this is not json {"
    r = await client.post("/sms/webhook", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 9. Failure path — paused user receives no reply (SIMULATOR_FAILURE)
# ---------------------------------------------------------------------------

async def test_paused_user_silenced(client, mock_send_sms, cleanup_phones):
    cleanup_phones.append(SIMULATOR_FAILURE)

    async with AsyncSessionLocal() as db:
        user = User(
            phone_number=SIMULATOR_FAILURE,
            onboarding_step=5,
            is_paused=True,
            name="Ghost",
        )
        db.add(user)
        await db.commit()

    r = await _post(client, SIMULATOR_FAILURE, "hello")
    assert r.status_code == 200
    assert len(mock_send_sms) == 0


# ---------------------------------------------------------------------------
# 10. Failure path — unknown SNS Type is ignored gracefully
# ---------------------------------------------------------------------------

async def test_unknown_sns_type_ignored(client):
    payload = build_sns_envelope(SIMULATOR_FAILURE, "test")
    payload["Type"] = "UnknownType"
    r = await client.post("/sms/webhook", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
