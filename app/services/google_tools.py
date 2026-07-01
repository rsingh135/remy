"""
LLM-callable Google integration tools.

Each function takes clean primitive parameters so the LLM tool executor in
tools.py can invoke them directly from the parsed tool_use block, with no
Google-specific knowledge required at the call site.

Design notes:
- Both functions delegate credential management entirely to `get_google_service`.
  The tools layer never touches tokens — that concern lives only in the service factory.
- `googleapiclient` calls are wrapped in lambdas passed to `asyncio.to_thread`.
  The lambda captures the service object (already built) and chains the method
  calls exactly as you would synchronously. This is the canonical pattern for
  the discovery-based client.
- `HttpError` from the Google client carries the HTTP status and response body.
  We propagate it as a 502 (Bad Gateway) — the upstream Google API is the
  external dependency that failed, not the Remy client's request.
- Gmail messages must be base64url-encoded (RFC 4648 §5). `base64.urlsafe_b64encode`
  on the raw MIME bytes produces the correct encoding. The `MIMEText` constructor
  accepts an explicit charset to ensure UTF-8 headers are set for non-ASCII bodies.
"""

import asyncio
import base64
import logging
from email.mime.text import MIMEText

from fastapi import HTTPException
from googleapiclient.errors import HttpError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.google_service import get_google_service

logger = logging.getLogger(__name__)


async def add_calendar_event(
    user_phone: str,
    summary: str,
    start_time_iso: str,
    end_time_iso: str,
    db: AsyncSession,
    description: str | None = None,
) -> dict:
    """
    Insert an event on the user's primary Google Calendar.

    Args:
        user_phone: Remy user identifier.
        summary: Event title displayed on the calendar.
        start_time_iso: ISO 8601 datetime with timezone offset, e.g. '2026-07-01T09:00:00-05:00'.
        end_time_iso: ISO 8601 datetime with timezone offset for event end.
        db: Active async SQLAlchemy session.
        description: Optional longer event description / notes.

    Returns:
        {"event_id": str, "link": str} — the created event's ID and calendar link.
    """
    service = await get_google_service(user_phone, "calendar", "v3", db)

    body: dict = {
        "summary": summary,
        "start": {"dateTime": start_time_iso},
        "end": {"dateTime": end_time_iso},
    }
    if description:
        body["description"] = description

    try:
        created = await asyncio.to_thread(
            lambda: service.events()
            .insert(calendarId="primary", body=body)
            .execute()
        )
    except HttpError as exc:
        logger.exception("Calendar insert failed for %s", user_phone)
        raise HTTPException(
            status_code=502, detail=f"Google Calendar error: {exc}"
        ) from exc

    logger.info("Calendar event created for %s: %s", user_phone, created.get("id"))
    return {"event_id": created["id"], "link": created.get("htmlLink", "")}


async def list_calendar_events(
    user_phone: str,
    time_min_iso: str,
    time_max_iso: str,
    db: AsyncSession,
    max_results: int = 10,
) -> dict:
    service = await get_google_service(user_phone, "calendar", "v3", db)

    try:
        result = await asyncio.to_thread(
            lambda: service.events()
            .list(
                calendarId="primary",
                timeMin=time_min_iso,
                timeMax=time_max_iso,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except HttpError as exc:
        logger.exception("Calendar list failed for %s", user_phone)
        raise HTTPException(
            status_code=502, detail=f"Google Calendar error: {exc}"
        ) from exc

    items = result.get("items", [])
    return {
        "events": [
            {
                "title": e.get("summary", "(no title)"),
                "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"),
                "end": e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"),
                "description": e.get("description"),
                "event_id": e.get("id"),
            }
            for e in items
        ],
        "count": len(items),
    }


async def send_gmail_message(
    user_phone: str,
    to_email: str,
    subject: str,
    body_text: str,
    db: AsyncSession,
) -> dict:
    """
    Send a plain-text email from the user's Gmail account.

    Args:
        user_phone: Remy user identifier.
        to_email: Recipient email address.
        subject: Email subject line.
        body_text: Plain-text body (UTF-8).
        db: Active async SQLAlchemy session.

    Returns:
        {"message_id": str, "thread_id": str} — the sent message's Gmail IDs.
    """
    service = await get_google_service(user_phone, "gmail", "v1", db)

    mime_msg = MIMEText(body_text, "plain", "utf-8")
    mime_msg["to"] = to_email
    mime_msg["subject"] = subject
    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()

    try:
        sent = await asyncio.to_thread(
            lambda: service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
    except HttpError as exc:
        logger.exception("Gmail send failed for %s → %s", user_phone, to_email)
        raise HTTPException(
            status_code=502, detail=f"Gmail error: {exc}"
        ) from exc

    logger.info("Gmail sent for %s → %s", user_phone, to_email)
    return {"message_id": sent["id"], "thread_id": sent.get("threadId", "")}
