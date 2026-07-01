"""
Tests for pause / resume via inbound SMS.

Contract:
  - Texting any pause keyword ("pause", "stop", "unsubscribe") sets is_paused=True
    and sends a confirmation message that tells the user how to resume.
  - Texting any resume keyword ("resume", "start", "unstop") clears is_paused and
    sends a confirmation. This must work even while the user is currently paused
    (the resume check runs before the silence guard).
  - While paused, any other inbound message produces no outbound reply.

Tests call handle_incoming_sms directly; outbound sending is suppressed by the
autouse mock_send_sms fixture in conftest.py.
"""

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.conversation import handle_incoming_sms

_PAUSE_PHONE = "+15550001002"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def fresh_user():
    """Create an active, onboarded user; delete after the test."""
    async with AsyncSessionLocal() as db:
        user = User(
            phone_number=_PAUSE_PHONE,
            name="Tester",
            onboarding_step=6,
            is_paused=False,
        )
        db.add(user)
        await db.commit()

    yield

    async with AsyncSessionLocal() as db:
        await db.execute(User.__table__.delete().where(User.phone_number == _PAUSE_PHONE))
        await db.commit()


@pytest.fixture
async def paused_user():
    """Create an already-paused user; delete after the test."""
    async with AsyncSessionLocal() as db:
        user = User(
            phone_number=_PAUSE_PHONE,
            name="Ghost",
            onboarding_step=6,
            is_paused=True,
        )
        db.add(user)
        await db.commit()

    yield

    async with AsyncSessionLocal() as db:
        await db.execute(User.__table__.delete().where(User.phone_number == _PAUSE_PHONE))
        await db.commit()


async def _get_user() -> User:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.phone_number == _PAUSE_PHONE))).scalar_one()


# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------

async def test_pause_keyword_sets_is_paused(fresh_user, mock_send_sms):
    async with AsyncSessionLocal() as db:
        await handle_incoming_sms(_PAUSE_PHONE, "pause", db)

    user = await _get_user()
    assert user.is_paused is True


async def test_stop_keyword_also_pauses_user(fresh_user, mock_send_sms):
    async with AsyncSessionLocal() as db:
        await handle_incoming_sms(_PAUSE_PHONE, "stop", db)

    user = await _get_user()
    assert user.is_paused is True


async def test_pause_sends_confirmation_with_resume_hint(fresh_user, mock_send_sms):
    async with AsyncSessionLocal() as db:
        await handle_incoming_sms(_PAUSE_PHONE, "pause", db)

    assert len(mock_send_sms) == 1
    assert mock_send_sms[0]["to"] == _PAUSE_PHONE
    # Confirmation must tell the user how to re-enable
    assert "resume" in mock_send_sms[0]["body"].lower()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

async def test_resume_keyword_clears_is_paused(paused_user, mock_send_sms):
    async with AsyncSessionLocal() as db:
        await handle_incoming_sms(_PAUSE_PHONE, "resume", db)

    user = await _get_user()
    assert user.is_paused is False


async def test_start_keyword_also_resumes_user(paused_user, mock_send_sms):
    async with AsyncSessionLocal() as db:
        await handle_incoming_sms(_PAUSE_PHONE, "start", db)

    user = await _get_user()
    assert user.is_paused is False


async def test_resume_sends_confirmation_reply(paused_user, mock_send_sms):
    async with AsyncSessionLocal() as db:
        await handle_incoming_sms(_PAUSE_PHONE, "resume", db)

    assert len(mock_send_sms) == 1
    assert mock_send_sms[0]["to"] == _PAUSE_PHONE
    assert len(mock_send_sms[0]["body"]) > 0


# ---------------------------------------------------------------------------
# Silence guard
# ---------------------------------------------------------------------------

async def test_paused_user_receives_no_reply_to_normal_message(paused_user, mock_send_sms):
    async with AsyncSessionLocal() as db:
        await handle_incoming_sms(_PAUSE_PHONE, "hey what's up", db)

    assert len(mock_send_sms) == 0


async def test_paused_user_remains_paused_after_normal_message(paused_user, mock_send_sms):
    async with AsyncSessionLocal() as db:
        await handle_incoming_sms(_PAUSE_PHONE, "hello", db)

    user = await _get_user()
    assert user.is_paused is True
