"""
Photon iMessage integration routes.

Two endpoints:

POST /photon/internal  — called by imessage_bridge.mjs (Node.js/spectrum-ts).
    The bridge receives iMessages via the Photon SDK, posts them here, and
    returns the reply text. The bridge then calls space.send() to deliver it.
    No HMAC needed — this endpoint is localhost-only (bridge and FastAPI run
    on the same machine / same VPC in production).

POST /photon/webhook   — legacy HMAC-signed webhook path, kept for reference.
    Not used when running imessage_bridge.mjs; can be removed once the bridge
    approach is confirmed stable.
"""

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.services.conversation import (
    get_or_create_user,
    handle_onboarding,
    handle_main_conversation,
    _split_reply,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_REPLAY_WINDOW_SECONDS = 600


# ---------------------------------------------------------------------------
# Internal endpoint — used by imessage_bridge.mjs
# ---------------------------------------------------------------------------

class _InternalRequest(BaseModel):
    sender_id: str
    message_text: str


def _is_duplicate(sender_id: str, message_text: str) -> bool:
    """Return True if this (sender, text) pair was seen within the last 10 seconds."""
    import hashlib
    import redis as redis_lib
    from app.config import get_settings
    key = "photon_dedup:" + hashlib.md5(f"{sender_id}:{message_text}".encode()).hexdigest()
    r = redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)
    # Atomic SET NX — prevents race condition between concurrent webhook deliveries.
    acquired = r.set(key, "1", nx=True, ex=60)
    return not acquired


@router.post("/photon/internal")
@limiter.limit("60/minute")
async def photon_internal(
    request: Request,
    body: _InternalRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Receive a message from the Node.js iMessage bridge and return a reply.
    The bridge calls space.send(reply) to deliver it back to the user.
    """
    sender_id = body.sender_id.strip()
    message_text = body.message_text.strip()

    if not sender_id or not message_text:
        return {"reply": None}

    if _is_duplicate(sender_id, message_text):
        logger.warning("Duplicate Photon event dropped for %s: %r", sender_id, message_text[:50])
        return {"replies": []}

    user, _ = await get_or_create_user(sender_id, db)

    if user.is_paused:
        return {"reply": None}

    if user.onboarding_step < 6:
        reply = await handle_onboarding(user, message_text, db)
    else:
        reply = await handle_main_conversation(user, message_text, db)

    return {"replies": _split_reply(reply)}


# ---------------------------------------------------------------------------
# HMAC-signed webhook path (kept for reference / future use)
# ---------------------------------------------------------------------------

def _verify_photon_signature(
    raw_body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: str,
) -> None:
    if not timestamp_header or not signature_header:
        raise HTTPException(status_code=401, detail="Missing Photon signature headers")

    try:
        ts = int(timestamp_header)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Photon timestamp header")

    if abs(time.time() - ts) > _REPLAY_WINDOW_SECONDS:
        raise HTTPException(status_code=401, detail="Photon webhook timestamp too old")

    signing_payload = f"v0:{timestamp_header}:".encode() + raw_body
    expected = "v0=" + hmac.new(
        secret.encode(), signing_payload, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid Photon webhook signature")


@router.post("/photon/webhook", status_code=200)
async def photon_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Legacy Spectrum webhook — superseded by imessage_bridge.mjs + /photon/internal.
    # Return immediately to prevent duplicate processing when both paths are active.
    return {"status": "ignored"}
    from app.config import get_settings
    raw_body = await request.body()
    s = get_settings()

    if s.PHOTON_WEBHOOK_SECRET:
        _verify_photon_signature(
            raw_body=raw_body,
            timestamp_header=request.headers.get("X-Spectrum-Timestamp"),
            signature_header=request.headers.get("X-Spectrum-Signature"),
            secret=s.PHOTON_WEBHOOK_SECRET,
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON payload: {exc}") from exc

    message_obj = payload.get("message", {})
    if message_obj.get("direction") != "inbound":
        return {"status": "ignored"}

    content = message_obj.get("content", {})
    if content.get("type") != "text":
        return {"status": "ignored"}

    sender_id = (message_obj.get("sender") or {}).get("id")
    message_text = content.get("text")

    if not sender_id or not message_text:
        return {"status": "ignored"}

    user, newly_created = await get_or_create_user(sender_id, db)
    if user.is_paused:
        return {"status": "ok"}

    if newly_created:
        logger.info("New user via webhook: %s", sender_id)
        return {"status": "ok"}

    if user.onboarding_step < 5:
        await handle_onboarding(user, message_text.strip(), db)
    else:
        await handle_main_conversation(user, message_text.strip(), db)

    return {"status": "ok"}
