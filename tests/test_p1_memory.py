"""
Tests for explicit memory recall and streak visibility.

recall_memories contract:
  - Returns all memories stored for the user across every category.
  - Returns an empty list when no memories exist, without error.
  - Caps the result at 30 entries so the response stays within SMS limits.
  - Every entry exposes category, text, and stored_at.

Streak visibility contract:
  - build_system_prompt always includes the user's current streak count so
    Claude can answer "what's my streak?" without calling a tool.
  - The prompt explains what a streak represents so Claude gives accurate
    context rather than just echoing the number.
"""

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.database import AsyncSessionLocal
from app.models.memory import Memory
from app.models.user import User
from app.services.tools import execute_tool

_PHONE = "+15550001003"
_FAKE_EMBEDDING = [0.0] * 1024  # valid 1024-dim vector; content irrelevant for retrieval tests


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def memory_user():
    async with AsyncSessionLocal() as db:
        user = User(phone_number=_PHONE, name="Rememberer", onboarding_step=6, timezone="UTC")
        db.add(user)
        await db.commit()

    yield

    async with AsyncSessionLocal() as db:
        await db.execute(User.__table__.delete().where(User.phone_number == _PHONE))
        await db.commit()


async def _insert_memory(category: str, text: str) -> None:
    async with AsyncSessionLocal() as db:
        db.add(Memory(user_phone=_PHONE, category=category, memory_text=text, embedding=_FAKE_EMBEDDING))
        await db.commit()


# ---------------------------------------------------------------------------
# recall_memories
# ---------------------------------------------------------------------------

async def test_recall_memories_returns_all_stored_memories(memory_user):
    await _insert_memory("fitness", "I run 5k three times a week")
    await _insert_memory("general", "I prefer morning workouts")
    await _insert_memory("academics", "Studying for LSAT in October")

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("recall_memories", {}, user, db)

    texts = [m["text"] for m in result["memories"]]
    assert "I run 5k three times a week" in texts
    assert "I prefer morning workouts" in texts
    assert "Studying for LSAT in October" in texts
    assert result["total"] == 3


async def test_recall_memories_returns_empty_when_no_memories(memory_user):
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("recall_memories", {}, user, db)

    assert result["memories"] == []
    assert result["total"] == 0


async def test_recall_memories_caps_at_30_entries(memory_user):
    """Inserting more than 30 memories must not cause the tool to return more than 30."""
    for i in range(35):
        await _insert_memory("general", f"memory number {i}")

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("recall_memories", {}, user, db)

    assert len(result["memories"]) == 30
    assert result["total"] == 30


async def test_recall_memories_each_entry_has_required_fields(memory_user):
    """Every returned entry must include category, text, and stored_at."""
    await _insert_memory("ideas", "Build a habit-tracking app")

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("recall_memories", {}, user, db)

    entry = next(m for m in result["memories"] if m["text"] == "Build a habit-tracking app")
    assert entry["category"] == "ideas"
    assert entry["stored_at"] is not None


async def test_recall_memories_includes_all_categories(memory_user):
    """recall_memories must not be silently scoped to a single category."""
    for cat in ("academics", "fitness", "ideas", "general"):
        await _insert_memory(cat, f"fact about {cat}")

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.phone_number == _PHONE))).scalar_one()
        result = await execute_tool("recall_memories", {}, user, db)

    returned_categories = {m["category"] for m in result["memories"]}
    assert returned_categories == {"academics", "fitness", "ideas", "general"}


