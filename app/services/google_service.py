"""
Google API service factory.

Design notes:
- `get_google_service` is the single entry point for all Google API calls.
  It owns the full credential lifecycle: fetch → check expiry → refresh → build.
- Token refresh uses `asyncio.to_thread` because `google.auth.transport.requests.Request`
  is built on the synchronous `requests` library. Calling it directly on the event
  loop would block all other coroutines for the duration of the network round-trip.
- `cache_discovery=False` in `build()` disables the default behaviour of writing
  Google's API discovery JSON to a local file. On containerised/ephemeral hosts
  (EC2, ECS) there is no guaranteed writable filesystem, and concurrent workers can
  corrupt the cache file causing hard-to-reproduce 'invalid discovery document' errors.
- The 60-second expiry buffer in `UserGoogleToken.is_expired()` prevents the edge
  case where a token is valid when we check it but expires before the downstream
  API call completes.
"""

import asyncio
import json
import logging
from datetime import timezone
from typing import Any

from fastapi import HTTPException
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.google_token import UserGoogleToken


class GoogleTokenExpiredError(Exception):
    """Raised when a Google refresh token has been revoked or is permanently invalid."""

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _build_client_config() -> dict:
    """Construct the client_config dict expected by Flow.from_client_config."""
    s = get_settings()
    return {
        "web": {
            "client_id": s.GOOGLE_CLIENT_ID,
            "client_secret": s.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [s.GOOGLE_REDIRECT_URI],
        }
    }


def _to_credentials(record: UserGoogleToken) -> Credentials:
    s = get_settings()
    return Credentials(
        token=record.access_token,
        refresh_token=record.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=s.GOOGLE_CLIENT_ID,
        client_secret=s.GOOGLE_CLIENT_SECRET,
        scopes=record.scopes_list,
    )


def _refresh_sync(creds: Credentials) -> None:
    """Blocking token refresh — must be run inside asyncio.to_thread."""
    creds.refresh(Request())


async def get_google_service(
    user_phone: str,
    service_name: str,
    version: str,
    db: AsyncSession,
) -> Any:
    """
    Return an authorised Google API service client for the given user.

    Args:
        user_phone: Remy user identifier (phone number PK).
        service_name: Google API name, e.g. "calendar" or "gmail".
        version: API version string, e.g. "v3" or "v1".
        db: Active async SQLAlchemy session.

    Returns:
        A `googleapiclient.discovery.Resource` ready for method chaining.

    Raises:
        HTTPException 401 if the user has not connected a Google account or if
            the refresh token is missing and the access token has expired.
        HTTPException 502 if the Google token refresh call fails.
    """
    result = await db.execute(
        select(UserGoogleToken).where(UserGoogleToken.user_phone == user_phone)
    )
    record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(
            status_code=401,
            detail=(
                "Google account not connected. "
                "Ask Remy to send you a Google connection link."
            ),
        )

    creds = _to_credentials(record)

    if record.is_expired():
        if not record.refresh_token:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Google access token expired and no refresh token is stored. "
                    "Please reconnect your Google account."
                ),
            )
        try:
            await asyncio.to_thread(_refresh_sync, creds)
        except RefreshError as exc:
            logger.warning("Google refresh token revoked for %s: %s", user_phone, exc)
            raise GoogleTokenExpiredError(
                f"Google authorization has expired for this user. They need to reconnect."
            ) from exc
        except Exception as exc:
            logger.exception("Token refresh failed for %s", user_phone)
            raise HTTPException(
                status_code=502,
                detail=f"Failed to refresh Google token: {exc}",
            ) from exc

        record.access_token = creds.token
        if creds.expiry:
            record.expires_at = creds.expiry.replace(tzinfo=timezone.utc)
        await db.commit()
        logger.info("Access token refreshed for %s", user_phone)

    return await asyncio.to_thread(
        build,
        service_name,
        version,
        credentials=creds,
        cache_discovery=False,
    )
