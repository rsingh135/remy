import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.sms import InboundSNSMessage, ParsedSMSBody
from app.services.conversation import handle_incoming_sms
from app.services.sns_verifier import verify_sns_signature

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhook", status_code=200)
async def sms_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    body = await request.body()
    raw = json.loads(body)
    envelope = InboundSNSMessage.model_validate(raw)

    await verify_sns_signature(envelope, raw)

    if envelope.Type == "SubscriptionConfirmation":
        if envelope.SubscribeURL:
            async with httpx.AsyncClient() as client:
                await client.get(envelope.SubscribeURL)
            logger.info("SNS subscription confirmed")
        return {"status": "confirmed"}

    if envelope.Type != "Notification":
        logger.warning("Unexpected SNS message type: %s", envelope.Type)
        return {"status": "ignored"}

    try:
        sms = ParsedSMSBody.model_validate_json(envelope.Message)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid SMS message format: {exc}") from exc

    await handle_incoming_sms(sms.originationNumber, sms.messageBody.strip(), db)

    return {"status": "ok"}
