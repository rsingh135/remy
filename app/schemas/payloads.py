from datetime import datetime
from typing import Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FitnessLogPayload(BaseModel):
    model_config = ConfigDict(strict=True)

    protein_grams: Optional[float] = None
    water_liters: Optional[float] = None
    workout_type: Optional[str] = None
    duration_minutes: Optional[int] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "FitnessLogPayload":
        if not any([self.protein_grams, self.water_liters, self.workout_type]):
            raise ValueError("At least one of protein_grams, water_liters, or workout_type is required")
        return self


class TaskPayload(BaseModel):
    model_config = ConfigDict(strict=True)

    description: str = Field(min_length=1, max_length=500)
    deadline: Optional[datetime] = None
    status: Literal["pending", "in_progress", "done"] = "pending"
    priority: Literal["low", "medium", "high"] = "medium"


class ReminderPayload(BaseModel):
    model_config = ConfigDict(strict=True)

    message: str = Field(min_length=1, max_length=160)
    execution_timestamp: datetime
    task_id: Optional[str] = None


PAYLOAD_SCHEMA_MAP: dict[str, Type[BaseModel]] = {
    "fitness_log": FitnessLogPayload,
    "task": TaskPayload,
    "reminder": ReminderPayload,
}
