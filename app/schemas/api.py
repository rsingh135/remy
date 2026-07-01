from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class OTPRequest(BaseModel):
    contact_id: str = Field(description="E.164 phone number or iCloud email")


class OTPVerify(BaseModel):
    contact_id: str
    otp: str = Field(min_length=6, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Health sync (Apple Health → backend)
# ---------------------------------------------------------------------------

class WorkoutEntry(BaseModel):
    type: str
    duration_minutes: int
    calories: Optional[int] = None


class HealthSyncPayload(BaseModel):
    date: date
    steps: Optional[int] = None
    sleep_hours: Optional[float] = None
    active_calories: Optional[int] = None
    hrv_avg: Optional[float] = None
    workouts: list[WorkoutEntry] = []


# ---------------------------------------------------------------------------
# Screen Time (DeviceActivity → backend)
# ---------------------------------------------------------------------------

class AppUsage(BaseModel):
    bundle_id: str
    display_name: str
    minutes: int


class ScreenTimePayload(BaseModel):
    date: date
    total_minutes: int
    pickups: Optional[int] = None
    top_apps: list[AppUsage] = []


# ---------------------------------------------------------------------------
# Dashboard response
# ---------------------------------------------------------------------------

class FitnessSummary(BaseModel):
    period: str
    total_protein_grams: float
    total_water_liters: float
    workout_count: int
    log_count: int


class DashboardResponse(BaseModel):
    streak_count: int
    open_task_count: int
    pending_reminder_count: int
    fitness_week: FitnessSummary
    todays_events: list[dict]
