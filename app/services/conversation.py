import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.vector_store import query_memories
from app.models.user import User
from app.schemas.memory import MemoryQueryResult
from app.services.bedrock import call_claude_with_tools
from app.services.onboarding import handle_onboarding
from app.services.sms_sender import send_sms as _send_sms_aws
from app.services.tools import execute_tool, is_affirmative

logger = logging.getLogger(__name__)

_PERSONA_INSTRUCTIONS: dict[str, str] = {
    "chill_coach": (
        "You're a chill, supportive friend. Laid-back energy. "
        "Celebrate wins without going overboard. Never preachy. "
        "Keep it light unless things get serious."
    ),
    "no_bs_peer": (
        "You're a straight-talking peer. No filler words. "
        "Get to the point fast. Call out excuses directly but without being mean. "
        "Be real, not harsh."
    ),
    "drill_sergeant": (
        "You're demanding and don't let anything slide. "
        "High standards, zero tolerance for excuses. Push hard every time. "
        "Short, punchy, intense."
    ),
}

_NIGHTLY_REDIS_KEY = "nightly_sent:{phone}:{date}"
_STREAK_GRACE_HOURS = 3


def build_system_prompt(user: User, memory_context: list[MemoryQueryResult]) -> str:
    from datetime import datetime, timezone

    persona = _PERSONA_INSTRUCTIONS.get(user.persona_style or "chill_coach", "")
    memories_text = "\n".join(
        f"- [{m.category}] {m.memory_text}" for m in memory_context
    ) or "None yet."

    objective_labels = {
        "study_buddy": "Study Buddy",
        "habit_architect": "Habit Architect",
        "idea_vault": "Idea Vault",
        "hybrid": "Hybrid",
    }

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return f"""You are Remy, texting with {user.name}.

PERSONA:
{persona}

USER PROFILE:
- Name: {user.name}
- Objective: {objective_labels.get(user.objective or '', 'Not set')}
- Core Goal: {user.core_goal or 'Not set'}
- Current Streak: {user.streak_count} days

CURRENT UTC TIME: {now_utc}

RELEVANT MEMORIES:
{memories_text}

HARD RULES — FOLLOW EXACTLY:
1. BREVITY: 1-3 sentences max. This is iMessage, not an email.
2. TONE: Maintain the persona above. Sound like a person, not a product.
3. NO AI FILLER: Never use "Certainly", "Of course", "Absolutely", "Great question",
   "I'd be happy to help", "I understand", "I appreciate", "As an AI",
   "Feel free to", "Please note", "I hope this helps", or any corporate/assistant language.
4. MATCH ENERGY: Mirror how they text — casual, lowercase, abbreviations are fine.
5. NAME: Use their name occasionally when it feels natural. Not in every message.
6. ACADEMIC GUARDRAIL: Never solve homework, write essays, or generate code.
   If asked, identify the concept and respond with one Socratic question only.
7. TOOLS: Use proactively for reminders, logs, schedules, memories.
   Resolve relative times to absolute UTC using CURRENT UTC TIME above.
"""


def _extract_text_content(response: dict) -> str:
    for block in response.get("content", []):
        if block.get("type") == "text":
            return block["text"].strip()
    return "Got it."


async def get_or_create_user(phone: str, db: AsyncSession) -> tuple[User, bool]:
    result = await db.execute(select(User).where(User.phone_number == phone))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(phone_number=phone)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True
    return user, False


async def handle_main_conversation(user: User, message: str, db: AsyncSession) -> str:
    await _check_and_update_streak(user, message, db)

    memory_context = await query_memories(user.phone_number, message, db, top_k=3)
    system_prompt = build_system_prompt(user, memory_context)
    messages = [{"role": "user", "content": message}]

    for _ in range(5):
        response = await call_claude_with_tools(messages, system_prompt)

        if response.get("stop_reason") == "end_turn":
            return _extract_text_content(response)

        if response.get("stop_reason") == "tool_use":
            messages.append({"role": "assistant", "content": response["content"]})

            tool_results = []
            for block in response["content"]:
                if block.get("type") == "tool_use":
                    result = await execute_tool(block["name"], block["input"], user, db)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": json.dumps(result),
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            return _extract_text_content(response)

    return "I hit a snag. Give me a second and try again?"


async def _check_and_update_streak(user: User, message: str, db: AsyncSession) -> None:
    if not is_affirmative(message):
        return

    import redis as redis_lib
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from app.config import get_settings

    s = get_settings()
    r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)

    today = date.today().isoformat()
    redis_key = f"nightly_sent:{user.phone_number}:{today}"

    if r.exists(redis_key):
        user.streak_count += 1
        await db.commit()
        r.delete(redis_key)


async def _send_reply(phone: str, message: str) -> None:
    from app.config import get_settings
    if get_settings().PHOTON_ENABLED:
        from app.services.photon_sender import send_via_photon
        await send_via_photon(phone, message)
    else:
        await _send_sms_aws(phone, message)


async def handle_incoming_sms(phone: str, message: str, db: AsyncSession) -> None:
    user, newly_created = await get_or_create_user(phone, db)

    if user.is_paused:
        return

    if newly_created:
        await _send_reply(phone, "Hey! I'm Remy, your personal Life OS. What should I call you?")
        return

    if user.onboarding_step < 5:
        reply = await handle_onboarding(user, message, db)
    else:
        reply = await handle_main_conversation(user, message, db)

    await _send_reply(user.phone_number, reply)
