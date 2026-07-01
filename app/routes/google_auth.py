"""
Google OAuth 2.0 authorization flow.

Design notes:
- State is a UUID stored in Redis (TTL=15 min) mapping to the user's phone number.
  This is the canonical CSRF guard for OAuth: an attacker cannot forge a callback
  because they cannot know the state value that was generated server-side for a
  specific phone.
- `access_type="offline"` + `prompt="consent"` are both required on every flow
  initiation. Without `prompt="consent"`, Google omits the refresh_token on
  re-authorisation (it only issues it on first consent), which would silently
  break token refresh after the 1-hour access token expires.
- Token upsert preserves an existing refresh_token if Google does not return a
  new one (belt-and-suspenders against future prompt param drift).
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.google_token import UserGoogleToken
from app.models.user import User
from app.services.google_service import SCOPES, _build_client_config
from app.services.sms_sender import send_sms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["google-auth"])

_OAUTH_STATE_TTL_SECONDS = 900  # 15 minutes


def _redis() -> redis_lib.Redis:
    return redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------------------
# Step 1 — initiate
# ---------------------------------------------------------------------------

@router.get("", summary="Redirect user to Google consent screen")
async def initiate_google_auth(
    phone: str = Query(..., description="Remy user's E.164 phone number"),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """
    Validate that the phone number belongs to a known user, then redirect
    to Google's OAuth consent screen.

    The `phone` parameter is embedded in the link Remy sends via SMS, e.g.:
        https://remy.rs1ngh.com/sms/auth/google?phone=%2B14254147755
    """
    result = await db.execute(select(User).where(User.contact_id == phone))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    state = str(uuid.uuid4())
    _redis().setex(f"oauth_state:{state}", _OAUTH_STATE_TTL_SECONDS, phone)

    s = get_settings()
    flow = Flow.from_client_config(_build_client_config(), scopes=SCOPES, state=state)
    flow.redirect_uri = s.GOOGLE_REDIRECT_URI

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="false",
    )

    logger.info("Google OAuth initiated for %s", phone)
    return RedirectResponse(url=auth_url, status_code=302)


# ---------------------------------------------------------------------------
# Step 2 — callback
# ---------------------------------------------------------------------------

@router.get("/callback", summary="Handle Google OAuth callback and persist tokens")
async def google_auth_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    Exchange the one-time authorization code for access + refresh tokens,
    then upsert them into `user_google_tokens`.

    Google may pass `error=access_denied` if the user cancels the consent
    screen — we surface that as a 400 before touching the database.
    """
    if error:
        logger.warning("Google OAuth denied: %s", error)
        raise HTTPException(status_code=400, detail=f"Google authorization denied: {error}")

    r = _redis()
    phone = r.get(f"oauth_state:{state}")
    if not phone:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OAuth state. Please request a new connection link.",
        )
    r.delete(f"oauth_state:{state}")

    s = get_settings()

    # Exchange the code — this is a synchronous network call; acceptable here
    # because it is a one-off user action, not a hot path.
    try:
        flow = Flow.from_client_config(_build_client_config(), scopes=SCOPES, state=state)
        flow.redirect_uri = s.GOOGLE_REDIRECT_URI
        flow.fetch_token(code=code)
        creds = flow.credentials
    except Exception as exc:
        logger.exception("Token exchange failed for %s", phone)
        raise HTTPException(
            status_code=502, detail=f"Google token exchange failed: {exc}"
        ) from exc

    # `credentials.expiry` is naive UTC from google-auth; make it aware.
    expires_at = (
        creds.expiry.replace(tzinfo=timezone.utc)
        if creds.expiry
        else datetime.now(timezone.utc) + timedelta(hours=1)
    )

    result = await db.execute(
        select(UserGoogleToken).where(UserGoogleToken.user_contact_id == phone)
    )
    record = result.scalar_one_or_none()

    if record is None:
        record = UserGoogleToken(user_contact_id=phone)
        db.add(record)

    record.access_token = creds.token
    # Preserve an existing refresh_token if Google didn't return a new one.
    record.refresh_token = creds.refresh_token or getattr(record, "refresh_token", None)
    record.expires_at = expires_at
    record.scopes = json.dumps(sorted(creds.scopes or SCOPES))

    await db.commit()
    logger.info("Google tokens persisted for %s", phone)

    try:
        await send_sms(
            phone,
            "Your Google account is now connected to Remy! "
            "I can manage your Calendar and Gmail.",
        )
    except Exception:
        logger.warning("Post-auth SMS failed for %s — tokens are saved regardless", phone)

    return HTMLResponse(
        content=(
            "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
            "<h2>Connected!</h2>"
            "<p>Your Google account is linked to Remy. You can close this tab.</p>"
            "</body></html>"
        ),
        status_code=200,
    )
