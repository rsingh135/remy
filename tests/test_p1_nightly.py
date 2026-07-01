"""
Tests for personalized nightly check-in messages and streak visibility.

_build_nightly_message contract:
  - Each user objective (study_buddy, habit_architect, idea_vault, hybrid) maps
    to its own distinctive message template.
  - An unrecognised or absent objective falls back to the generic default.
  - The user's name is always present in the message.
  - The user's core_goal is embedded in the message, truncated to 40 chars so
    the full SMS stays within a single segment.
  - A None core_goal is replaced with a neutral fallback phrase rather than
    raising an error or leaving a bare format placeholder.

Streak visibility contract (system prompt):
  - build_system_prompt always embeds the current streak count so Claude can
    answer "what's my streak?" directly from context without a tool call.
  - The prompt explains what a streak represents so Claude provides accurate
    context rather than just echoing a number.
"""

import pytest

from app.models.user import User
from app.services.conversation import build_system_prompt
from app.tasks.nightly import _build_nightly_message


# ---------------------------------------------------------------------------
# Objective-specific templates
# ---------------------------------------------------------------------------

def test_study_buddy_message_references_studying():
    msg = _build_nightly_message("Alex", "study_buddy", "ace my finals")
    assert "Alex" in msg
    assert "studying" in msg.lower() or "study" in msg.lower()


def test_habit_architect_message_references_habits():
    msg = _build_nightly_message("Sam", "habit_architect", "meditate daily")
    assert "Sam" in msg
    assert "habit" in msg.lower()


def test_idea_vault_message_references_capturing():
    msg = _build_nightly_message("Jordan", "idea_vault", "ship my side project")
    assert "Jordan" in msg
    assert any(word in msg.lower() for word in ("capture", "idea", "keeping"))


def test_hybrid_message_references_productivity():
    msg = _build_nightly_message("Priya", "hybrid", "stay consistent")
    assert "Priya" in msg
    assert any(word in msg.lower() for word in ("productive", "needle", "moved", "highlight"))


# ---------------------------------------------------------------------------
# Fallback for unknown / absent objective
# ---------------------------------------------------------------------------

def test_unknown_objective_uses_default_message():
    msg = _build_nightly_message("Ren", "nonexistent_mode", "run a marathon")
    assert "Ren" in msg
    # The default template references hitting their goal
    assert "goal" in msg.lower() or "hit" in msg.lower()


def test_none_objective_uses_default_message():
    msg = _build_nightly_message("Eli", None, "finish the book")
    assert "Eli" in msg
    assert "goal" in msg.lower() or "hit" in msg.lower()


# ---------------------------------------------------------------------------
# Core goal handling
# ---------------------------------------------------------------------------

def test_core_goal_is_included_in_message():
    msg = _build_nightly_message("Dev", "habit_architect", "drink 3 litres of water")
    assert "drink 3 litres of water" in msg


def test_long_core_goal_is_truncated_to_40_chars():
    long_goal = "a" * 80
    msg = _build_nightly_message("Dev", "habit_architect", long_goal)
    # The embedded goal snippet must be ≤ 40 characters
    assert long_goal not in msg
    assert "a" * 40 in msg
    assert "a" * 41 not in msg


def test_none_core_goal_does_not_raise_and_produces_valid_message():
    msg = _build_nightly_message("Casey", "study_buddy", None)
    assert isinstance(msg, str)
    assert len(msg) > 0
    assert "{goal}" not in msg  # placeholder must be resolved


# ---------------------------------------------------------------------------
# Name interpolation
# ---------------------------------------------------------------------------

def test_user_name_always_present_in_every_objective():
    objectives = ["study_buddy", "habit_architect", "idea_vault", "hybrid", None, "unknown"]
    for obj in objectives:
        msg = _build_nightly_message("Morgan", obj, "some goal")
        assert "Morgan" in msg, f"Name missing for objective={obj!r}"


# ---------------------------------------------------------------------------
# Streak visibility — system prompt content
# ---------------------------------------------------------------------------

def test_system_prompt_contains_streak_count():
    """The streak count must be in the prompt so Claude can answer
    'what's my streak?' without a tool call."""
    user = User(
        phone_number="+10000000001",
        name="Streaker",
        streak_count=12,
        persona_style="chill_coach",
    )
    prompt = build_system_prompt(user, memory_context=[])
    assert "12" in prompt


def test_system_prompt_explains_what_a_streak_represents():
    """Claude must be able to describe what a streak means, not just cite a
    number, so the prompt must reference the mechanism that drives it."""
    user = User(
        phone_number="+10000000001",
        name="Streaker",
        streak_count=3,
        persona_style="chill_coach",
    )
    prompt = build_system_prompt(user, memory_context=[])
    assert "nightly" in prompt.lower() or "check-in" in prompt.lower() or "goal" in prompt.lower()


def test_system_prompt_directs_claude_to_answer_streak_from_profile():
    """The tool instructions must tell Claude to read streak from USER PROFILE
    rather than calling a tool, avoiding a wasted round-trip."""
    user = User(
        phone_number="+10000000001",
        name="Streaker",
        streak_count=5,
        persona_style="no_bs_peer",
    )
    prompt = build_system_prompt(user, memory_context=[])
    assert "streak" in prompt.lower()
    assert "USER PROFILE" in prompt
