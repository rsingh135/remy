import json
import os
import uuid
from datetime import datetime, timezone

# Must be set before any app imports so the lru_cached Settings picks it up
os.environ["DEV_SKIP_SNS_VERIFY"] = "true"

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import delete

load_dotenv("/Users/ranveersingh/remy/.env", override=False)

from app.config import get_settings
get_settings.cache_clear()

from app.database import AsyncSessionLocal
from app.main import app
from app.models.user import User

# AWS SMS simulator numbers — safe to use as origination in sandbox
SIMULATOR_SUCCESS = "+14254147755"   # EUM treats delivery as successful
SIMULATOR_FAILURE = "+14254147167"   # EUM treats delivery as failed
REMY_NUMBER = get_settings().EUM_ORIGINATION_IDENTITY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_sns_envelope(origination: str, body: str) -> dict:
    """Build a realistic SNS Notification envelope matching EUM inbound format."""
    inner_message = json.dumps({
        "originationNumber": origination,
        "destinationNumber": REMY_NUMBER,
        "messageBody": body,
        "messageKeyword": None,
        "inboundMessageId": str(uuid.uuid4()),
        "previousPublishedMessageId": None,
    })
    return {
        "Type": "Notification",
        "MessageId": str(uuid.uuid4()),
        "TopicArn": "arn:aws:sns:us-east-1:536284936795:remy-inbound",
        "Message": inner_message,
        "Timestamp": datetime.now(timezone.utc).isoformat(),
        "SignatureVersion": "1",
        "Signature": "TESTSIGNATURE==",
        "SigningCertURL": "https://sns.amazonaws.com/SimpleNotificationService-test.pem",
        "UnsubscribeURL": "https://sns.amazonaws.com/unsubscribe",
    }


def build_subscription_confirmation(subscribe_url: str = "https://httpbin.org/get") -> dict:
    return {
        "Type": "SubscriptionConfirmation",
        "MessageId": str(uuid.uuid4()),
        "TopicArn": "arn:aws:sns:us-east-1:536284936795:remy-inbound",
        "Message": "You have chosen to subscribe to the topic.",
        "Timestamp": datetime.now(timezone.utc).isoformat(),
        "SignatureVersion": "1",
        "Signature": "TESTSIGNATURE==",
        "SigningCertURL": "https://sns.amazonaws.com/SimpleNotificationService-test.pem",
        "SubscribeURL": subscribe_url,
        "Token": "test-confirmation-token",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    """Async HTTP client backed by the ASGI app — no real server needed."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def mock_send_sms(mocker):
    """
    Patch outbound SMS globally for every test.
    Returns a list that accumulates every (to, body) pair sent.
    """
    sent = []

    async def _fake(phone: str, message: str) -> None:
        sent.append({"to": phone, "body": message})

    mocker.patch("app.services.sms_sender.send_sms", side_effect=_fake)
    mocker.patch("app.services.conversation.send_sms", side_effect=_fake)
    return sent


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


def _sync_delete(phones: list[str]) -> None:
    """Delete User rows synchronously via psycopg2 (safe in any teardown context)."""
    if not phones:
        return
    from sqlalchemy import create_engine, text

    s = get_settings()
    engine = create_engine(s.DATABASE_URL_SYNC)
    with engine.connect() as conn:
        for phone in phones:
            conn.execute(text("DELETE FROM users WHERE phone_number = :p"), {"p": phone})
        conn.commit()
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def pre_clean_simulators():
    """Delete any leftover simulator rows before the session starts.

    Guards against stale data from a prior run whose async teardown failed.
    """
    _sync_delete([SIMULATOR_SUCCESS, SIMULATOR_FAILURE])


@pytest.fixture
def cleanup_phones():
    """Yield a list; after the test, delete any User rows with those phones."""
    phones: list[str] = []
    yield phones
    _sync_delete(phones)
