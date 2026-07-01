from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.event import Event
from app.models.user import User
from app.schemas.api import DashboardResponse, FitnessSummary, HealthSyncPayload, ScreenTimePayload

router = APIRouter(tags=["api"])


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@router.get("/me", summary="Get user profile")
async def get_me(user: User = Depends(get_current_user)) -> dict:
    return {
        "contact_id": user.contact_id,
        "name": user.name,
        "persona_style": user.persona_style,
        "objective": user.objective,
        "core_goal": user.core_goal,
        "streak_count": user.streak_count,
        "gmail_read_enabled": user.gmail_read_enabled,
        "timezone": user.timezone,
        "onboarding_complete": user.onboarding_step >= 6,
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_model=DashboardResponse, summary="Aggregated stats for the app home screen")
async def get_dashboard(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    from datetime import timedelta

    now = datetime.now(tz=timezone.utc)
    week_ago = now - timedelta(days=7)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Open task count
    task_result = await db.execute(
        select(Event)
        .where(Event.user_contact_id == user.contact_id)
        .where(Event.event_type == "task")
        .where(Event.payload["status"].astext != "done")
    )
    open_task_count = len(task_result.scalars().all())

    # Pending reminder count
    from sqlalchemy import cast
    from sqlalchemy import DateTime as SADateTime
    execution_ts = cast(Event.payload["execution_timestamp"].astext, SADateTime(timezone=True))
    reminder_result = await db.execute(
        select(Event)
        .where(Event.user_contact_id == user.contact_id)
        .where(Event.event_type == "reminder")
        .where(execution_ts > now)
    )
    pending_reminder_count = len(reminder_result.scalars().all())

    # 7-day fitness summary
    fitness_result = await db.execute(
        select(Event)
        .where(Event.user_contact_id == user.contact_id)
        .where(Event.event_type == "fitness_log")
        .where(Event.timestamp >= week_ago)
    )
    fitness_events = fitness_result.scalars().all()
    total_protein = round(sum(e.payload.get("protein_grams") or 0 for e in fitness_events), 1)
    total_water = round(sum(e.payload.get("water_liters") or 0 for e in fitness_events), 2)
    workouts = [e for e in fitness_events if e.payload.get("workout_type")]

    fitness_week = FitnessSummary(
        period="week",
        total_protein_grams=total_protein,
        total_water_liters=total_water,
        workout_count=len(workouts),
        log_count=len(fitness_events),
    )

    # Today's events
    today_end = today_start.replace(hour=23, minute=59, second=59)
    schedule_result = await db.execute(
        select(Event)
        .where(Event.user_contact_id == user.contact_id)
        .where(Event.timestamp >= today_start)
        .where(Event.timestamp <= today_end)
        .order_by(Event.timestamp)
    )
    todays_events = [
        {"event_type": e.event_type, "payload": e.payload, "timestamp": e.timestamp.isoformat()}
        for e in schedule_result.scalars().all()
    ]

    return DashboardResponse(
        streak_count=user.streak_count,
        open_task_count=open_task_count,
        pending_reminder_count=pending_reminder_count,
        fitness_week=fitness_week,
        todays_events=todays_events,
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@router.get("/events", summary="Paginated event history")
async def get_events(
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    limit = min(limit, 200)
    stmt = (
        select(Event)
        .where(Event.user_contact_id == user.contact_id)
        .order_by(Event.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    if event_type:
        stmt = stmt.where(Event.event_type == event_type)

    result = await db.execute(stmt)
    events = result.scalars().all()
    return {
        "events": [
            {"id": e.id, "event_type": e.event_type, "payload": e.payload, "timestamp": e.timestamp.isoformat()}
            for e in events
        ],
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@router.get("/tasks", summary="Open tasks")
async def get_tasks(
    status: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = (
        select(Event)
        .where(Event.user_contact_id == user.contact_id)
        .where(Event.event_type == "task")
        .order_by(Event.timestamp.desc())
    )
    if status:
        stmt = stmt.where(Event.payload["status"].astext == status)
    else:
        stmt = stmt.where(Event.payload["status"].astext != "done")

    result = await db.execute(stmt)
    events = result.scalars().all()
    return {
        "tasks": [
            {
                "event_id": e.id,
                "description": e.payload.get("description"),
                "status": e.payload.get("status"),
                "priority": e.payload.get("priority"),
                "deadline": e.payload.get("deadline"),
                "created_at": e.timestamp.isoformat(),
            }
            for e in events
        ],
    }


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

@router.get("/reminders", summary="Pending reminders")
async def get_reminders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from sqlalchemy import cast
    from sqlalchemy import DateTime as SADateTime

    now = datetime.now(tz=timezone.utc)
    execution_ts = cast(Event.payload["execution_timestamp"].astext, SADateTime(timezone=True))
    result = await db.execute(
        select(Event)
        .where(Event.user_contact_id == user.contact_id)
        .where(Event.event_type == "reminder")
        .where(execution_ts > now)
        .order_by(execution_ts)
    )
    events = result.scalars().all()
    return {
        "reminders": [
            {
                "task_id": e.payload.get("task_id"),
                "message": e.payload.get("message"),
                "eta": e.payload.get("execution_timestamp"),
            }
            for e in events
        ],
    }


# ---------------------------------------------------------------------------
# Apple Health sync
# ---------------------------------------------------------------------------

@router.post("/health-sync", summary="Batch ingest Apple Health data")
async def health_sync(
    body: HealthSyncPayload,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    event = Event(
        user_contact_id=user.contact_id,
        event_type="health_sync",
        payload=body.model_dump(mode="json"),
    )
    db.add(event)
    await db.commit()
    return {"status": "synced", "date": body.date.isoformat()}


# ---------------------------------------------------------------------------
# Screen Time
# ---------------------------------------------------------------------------

@router.post("/screentime", summary="Store daily Screen Time data from iOS app")
async def post_screentime(
    body: ScreenTimePayload,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    event = Event(
        user_contact_id=user.contact_id,
        event_type="screen_time",
        payload=body.model_dump(mode="json"),
    )
    db.add(event)
    await db.commit()
    return {"status": "stored", "date": body.date.isoformat()}


@router.get("/screentime", summary="Screen Time history (last 30 days)")
async def get_screentime(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from datetime import timedelta

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
    result = await db.execute(
        select(Event)
        .where(Event.user_contact_id == user.contact_id)
        .where(Event.event_type == "screen_time")
        .where(Event.timestamp >= cutoff)
        .order_by(Event.timestamp.desc())
    )
    events = result.scalars().all()
    return {
        "screentime": [
            {
                "date": e.payload.get("date"),
                "total_minutes": e.payload.get("total_minutes"),
                "pickups": e.payload.get("pickups"),
                "top_apps": e.payload.get("top_apps", []),
            }
            for e in events
        ],
    }
