"""
Google OAuth + Calendar + Gmail integration tests.

All external Google API calls are mocked so no real Google account is needed.
Real RDS is used for token persistence.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.database import AsyncSessionLocal
from app.models.google_token import UserGoogleToken
from app.models.user import User
from app.services.google_tools import add_calendar_event, send_gmail_message

_TEST_PHONE = "+15550000043"
_FUTURE_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def google_user():
    """Create an onboarded user for Google tests, then clean up."""
    async with AsyncSessionLocal() as db:
        user = User(
            phone_number=_TEST_PHONE,
            name="GoogleTester",
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


@pytest.fixture
async def google_token(google_user):
    """Insert a valid (non-expired) Google token for the test user."""
    async with AsyncSessionLocal() as db:
        token = UserGoogleToken(
            user_phone=_TEST_PHONE,
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            expires_at=_FUTURE_EXPIRY,
            scopes=json.dumps([
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/gmail.modify",
            ]),
        )
        db.add(token)
        await db.commit()


# ---------------------------------------------------------------------------
# Google OAuth initiation
# ---------------------------------------------------------------------------

@pytest.fixture
async def http_client():
    import httpx
    from app.main import app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


async def test_google_auth_initiate_unknown_user(http_client):
    """Unknown phone number returns 404."""
    r = await http_client.get("/sms/auth/google?phone=%2B15550000000")
    assert r.status_code == 404


async def test_google_auth_initiate_known_user(http_client, google_user):
    """Known user gets redirected to Google consent URL."""
    with patch("app.routes.google_auth.Flow") as mock_flow_cls:
        mock_flow = MagicMock()
        mock_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?fake=1", "state123")
        mock_flow_cls.from_client_config.return_value = mock_flow

        r = await http_client.get(
            f"/sms/auth/google?phone={_TEST_PHONE.replace('+', '%2B')}",
            follow_redirects=False,
        )

    assert r.status_code in (302, 307)
    assert "accounts.google.com" in r.headers["location"]


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

async def test_token_written_to_db(google_user):
    """UserGoogleToken can be inserted and retrieved for a user."""
    async with AsyncSessionLocal() as db:
        token = UserGoogleToken(
            user_phone=_TEST_PHONE,
            access_token="access-abc",
            refresh_token="refresh-abc",
            expires_at=_FUTURE_EXPIRY,
            scopes=json.dumps(["https://www.googleapis.com/auth/calendar"]),
        )
        db.add(token)
        await db.commit()

        record = (await db.execute(
            select(UserGoogleToken).where(UserGoogleToken.user_phone == _TEST_PHONE)
        )).scalar_one_or_none()

    assert record is not None
    assert record.access_token == "access-abc"
    assert not record.is_expired()

    # Cleanup
    async with AsyncSessionLocal() as db:
        await db.execute(
            UserGoogleToken.__table__.delete().where(UserGoogleToken.user_phone == _TEST_PHONE)
        )
        await db.commit()


async def test_token_is_expired_detection(google_user):
    """is_expired() returns True when token expires within buffer window."""
    async with AsyncSessionLocal() as db:
        expired_token = UserGoogleToken(
            user_phone=_TEST_PHONE,
            access_token="stale-token",
            refresh_token="refresh-abc",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=30),
            scopes=json.dumps(["https://www.googleapis.com/auth/calendar"]),
        )
        db.add(expired_token)
        await db.commit()

        record = (await db.execute(
            select(UserGoogleToken).where(UserGoogleToken.user_phone == _TEST_PHONE)
        )).scalar_one()

    assert record.is_expired()

    async with AsyncSessionLocal() as db:
        await db.execute(
            UserGoogleToken.__table__.delete().where(UserGoogleToken.user_phone == _TEST_PHONE)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Calendar event creation
# ---------------------------------------------------------------------------

async def test_add_calendar_event_calls_google_api(google_user, google_token):
    """add_calendar_event passes the correct body to Google Calendar API."""
    fake_event = {"id": "evt123", "htmlLink": "https://calendar.google.com/event?eid=evt123"}

    mock_service = MagicMock()
    mock_service.events.return_value.insert.return_value.execute.return_value = fake_event

    with patch("app.services.google_tools.get_google_service", new_callable=AsyncMock) as mock_svc:
        mock_svc.return_value = mock_service
        async with AsyncSessionLocal() as db:
            result = await add_calendar_event(
                user_phone=_TEST_PHONE,
                summary="SWE Prep",
                start_time_iso="2026-07-01T10:00:00-05:00",
                end_time_iso="2026-07-01T11:00:00-05:00",
                db=db,
            )

    assert result["event_id"] == "evt123"
    assert "calendar.google.com" in result["link"]


async def test_add_calendar_event_with_description(google_user, google_token):
    """Optional description is included in the event body."""
    fake_event = {"id": "evt456", "htmlLink": "https://calendar.google.com/event?eid=evt456"}

    mock_service = MagicMock()
    insert_mock = mock_service.events.return_value.insert
    insert_mock.return_value.execute.return_value = fake_event

    with patch("app.services.google_tools.get_google_service", new_callable=AsyncMock) as mock_svc:
        mock_svc.return_value = mock_service
        async with AsyncSessionLocal() as db:
            result = await add_calendar_event(
                user_phone=_TEST_PHONE,
                summary="Study session",
                start_time_iso="2026-07-02T09:00:00-05:00",
                end_time_iso="2026-07-02T10:00:00-05:00",
                description="Focus: dynamic programming",
                db=db,
            )

    assert result["event_id"] == "evt456"
    call_body = insert_mock.call_args.kwargs["body"]
    assert call_body["description"] == "Focus: dynamic programming"


# ---------------------------------------------------------------------------
# Gmail send
# ---------------------------------------------------------------------------

async def test_send_gmail_message_encodes_correctly(google_user, google_token):
    """send_gmail_message base64url-encodes the MIME message and calls Gmail API."""
    fake_sent = {"id": "msg001", "threadId": "thread001"}

    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.send.return_value.execute.return_value = fake_sent

    with patch("app.services.google_tools.get_google_service", new_callable=AsyncMock) as mock_svc:
        mock_svc.return_value = mock_service
        async with AsyncSessionLocal() as db:
            result = await send_gmail_message(
                user_phone=_TEST_PHONE,
                to_email="test@example.com",
                subject="Remy test",
                body_text="Hello from Remy",
                db=db,
            )

    assert result["message_id"] == "msg001"
    assert result["thread_id"] == "thread001"

    # Verify the send call included a base64-encoded 'raw' field
    send_call = mock_service.users.return_value.messages.return_value.send
    call_body = send_call.call_args.kwargs["body"]
    assert "raw" in call_body
    # raw is the whole MIME message base64url-encoded; decode and check headers
    import base64
    decoded = base64.urlsafe_b64decode(call_body["raw"] + "==").decode()
    assert "test@example.com" in decoded
    assert "Remy test" in decoded


async def test_send_gmail_no_token_returns_401(google_user):
    """Calling a Google tool without a connected account raises 401."""
    from fastapi import HTTPException

    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            await send_gmail_message(
                user_phone=_TEST_PHONE,
                to_email="test@example.com",
                subject="Should fail",
                body_text="No token",
                db=db,
            )

    assert exc_info.value.status_code == 401
