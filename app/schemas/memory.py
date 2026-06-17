from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MemoryStoreRequest(BaseModel):
    category: Literal["academics", "fitness", "ideas", "general"]
    memory_text: str = Field(min_length=1, max_length=2000)


class MemoryQueryResult(BaseModel):
    memory_text: str
    category: str
    similarity: float
    created_at: datetime
