import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.auth.jwt import create_token, generate_otp, store_otp, verify_otp
from app.database import get_db
from app.models.user import User
from app.schemas.api import OTPRequest, OTPVerify, TokenResponse
from app.tasks.reminders import _send_message

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["api-auth"])


@router.post("/request-otp", summary="Send a 6-digit OTP via SMS/iMessage")
async def request_otp(
    body: OTPRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.contact_id == body.contact_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="No Remy account found for this contact ID")

    otp = generate_otp()
    store_otp(body.contact_id, otp)

    try:
        _send_message(body.contact_id, f"Your Remy code: {otp}. Expires in 10 minutes.")
    except Exception:
        logger.exception("Failed to send OTP to %s", body.contact_id)
        raise HTTPException(status_code=502, detail="Failed to deliver OTP")

    logger.info("OTP sent to %s", body.contact_id)
    return {"detail": "OTP sent"}


@router.post("/verify-otp", response_model=TokenResponse, summary="Exchange OTP for JWT")
async def verify_otp_route(body: OTPVerify) -> TokenResponse:
    if not verify_otp(body.contact_id, body.otp):
        raise HTTPException(status_code=401, detail="Invalid or expired OTP")

    token = create_token(body.contact_id)
    logger.info("JWT issued for %s", body.contact_id)
    return TokenResponse(access_token=token)
