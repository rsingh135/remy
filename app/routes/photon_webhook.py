"""
Photon iMessage webhook receiver.

Photon signs each delivery with HMAC-SHA256 over the string
  v0:{X-Spectrum-Timestamp}:{raw_body}
and sends the hex digest in X-Spectrum-Signature as "v0=<hex>".

A 5-minute replay window guards against replayed requests.
The space_id returned in the payload is cached in Redis so the
outbound sender can look it up without an extra Photon API call.
"""

import hashlib
import hmac
import json
import logging
import time

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.conversation import handle_incoming_sms

router = APIRouter()
logger = logging.getLogger(__name__)

_REPLAY_WINDOW_SECONDS = 300


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
    space_obj = payload.get("space", {})

    if message_obj.get("direction") != "inbound":
        return {"status": "ignored"}

    content = message_obj.get("content", {})
    if content.get("type") != "text":
        return {"status": "ignored"}

    sender_id: str | None = (message_obj.get("sender") or {}).get("id")
    message_text: str | None = content.get("text")
    space_id: str | None = space_obj.get("id")

    if not sender_id or not message_text:
        logger.warning("Photon webhook missing sender_id or text: %s", payload)
        return {"status": "ignored"}

    if space_id:
        r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)
        r.set(f"photon_space:{sender_id}", space_id, ex=86400)

    await handle_incoming_sms(sender_id, message_text.strip(), db)
    return {"status": "ok"}
