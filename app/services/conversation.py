import json
import logging
import re

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

_PAUSE_KEYWORDS = {"pause", "stop", "unsubscribe"}
_RESUME_KEYWORDS = {"resume", "start", "unstop"}

_CONV_HISTORY_KEY = "conv_history:{phone}"
_CONV_MAX_TURNS = 6   # 3 exchanges (user + assistant per exchange)
_CONV_TTL = 86400     # 24 hours


def _get_conv_history(phone: str, r) -> list[dict]:
    raw = r.lrange(_CONV_HISTORY_KEY.format(phone=phone), 0, -1)
    history = []
    for item in raw[-_CONV_MAX_TURNS:]:
        try:
            history.append(json.loads(item))
        except (json.JSONDecodeError, ValueError):
            pass
    return history


def _append_conv_history(phone: str, user_msg: str, assistant_msg: str, r) -> None:
    key = _CONV_HISTORY_KEY.format(phone=phone)
    r.rpush(key, json.dumps({"role": "user", "content": user_msg}))
    r.rpush(key, json.dumps({"role": "assistant", "content": assistant_msg}))
    r.ltrim(key, -_CONV_MAX_TURNS, -1)
    r.expire(key, _CONV_TTL)


def _split_reply(text: str) -> list[str]:
    # Paragraph breaks → separate messages; single newlines within a paragraph → also breaks
    chunks: list[str] = []
    for para in text.split("\n\n"):
        for line in para.split("\n"):
            line = line.strip()
            if line:
                chunks.append(line)

    result: list[str] = []
    for chunk in chunks:
        if len(chunk) <= 220:
            result.append(chunk)
        else:
            sentences = re.split(r'(?<=[.!?])\s+', chunk)
            current = ""
            for s in sentences:
                candidate = (current + " " + s) if current else s
                if len(candidate) <= 220:
                    current = candidate
                else:
                    if current:
                        result.append(current)
                    current = s
            if current:
                result.append(current)

    return result[:4] or [""]


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
- Current Streak: {user.streak_count} days (increments each day they confirm hitting their goal after the nightly check-in)

CURRENT UTC TIME: {now_utc}

RELEVANT MEMORIES:
{memories_text}

HARD RULES — FOLLOW EXACTLY:
1. BREVITY: 1-3 sentences max. This is iMessage, not an email.
2. TONE: Maintain the persona above. Sound like a person, not a product.
3. NO AI FILLER — never use any of these:
   - Filler openers: "Certainly", "Of course", "Absolutely", "Sure", "Great question",
     "I'd be happy to", "I understand", "I appreciate", "As an AI", "Feel free to",
     "Please note", "I hope this helps", "That being said", "Additionally",
     "Furthermore", "Moreover", "It's worth noting", "It's important to note"
   - Em dashes (—) — never use them, ever. Use a comma or period instead.
   - Semicolons — people don't text with semicolons.
   - Bullet points in regular conversation — only use them for structured choices.
   - Exclamation marks more than once per reply.
   - Corporate sign-offs or meta-commentary about your own response.
4. MATCH ENERGY: Mirror how they text — casual, lowercase, abbreviations are fine.
5. NAME: Use their name occasionally when it feels natural. Not in every message.
6. ACADEMIC GUARDRAIL: Never solve homework, write essays, or generate code.
   If asked, identify the concept and respond with one Socratic question only.
7. TOOLS — NEVER fake it, ALWAYS call the real tool:
   - User says "remind me" → call add_reminder EXACTLY ONCE. Never call add_reminder more than once per message, even if the phrasing is ambiguous.
   - User asks about their day / schedule / what's on / what they have → call query_schedule with today's date.
   - User wants to see their reminders / what's scheduled → call list_reminders.
   - User wants to cancel a reminder → call list_reminders first to identify it, then call cancel_reminder with the task_id.
   - User asks what you remember about them → call recall_memories and summarize the results.
   - User asks about their streak → answer directly from USER PROFILE above. Do not call a tool.
   - User logs workout / water / food → call log_event.
   - User asks to see their tasks / to-do list → call list_tasks.
   - User marks a task done or changes its priority → call list_tasks to find the event_id, then call update_task.
   - User asks about fitness stats, protein, water, workouts → call query_fitness_summary with the relevant period.
   - User asks to change their persona, goal, or focus → call update_profile with the correct field and value.
   - User asks what's on their calendar / what's coming up → call list_calendar_events with appropriate ISO datetime range.
   - User shares a personal fact (habit, goal, deadline, preference, struggle, win) → call store_memory.
   - "connect Google" / "add to calendar" / "send email" → call the relevant Google tool.
   - User asks to check email / read inbox → call read_gmail.
   - User says "enable Gmail reading" or "disable Gmail reading" → call update_profile with field="gmail_read_enabled" and value="true" or "false".
   Resolve ALL relative times ("tomorrow", "in 2 hours") to absolute UTC ISO strings
   using CURRENT UTC TIME above before passing to any tool.
"""


def _extract_text_content(response: dict) -> str:
    for block in response.get("content", []):
        if block.get("type") == "text":
            return block["text"].strip()
    return "Got it."


async def get_or_create_user(phone: str, db: AsyncSession) -> tuple[User, bool]:
    result = await db.execute(select(User).where(User.contact_id == phone))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(contact_id=phone)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user, True
    return user, False


def _token_budget_key(phone: str) -> str:
    from datetime import date
    return f"bedrock_tokens:{phone}:{date.today().isoformat()}"


def _get_token_usage(phone: str, r) -> int:
    val = r.get(_token_budget_key(phone))
    return int(val) if val else 0


def _increment_token_usage(phone: str, tokens: int, r) -> int:
    key = _token_budget_key(phone)
    new_total = r.incrby(key, tokens)
    r.expire(key, 90000)  # 25h TTL — covers across midnight safely
    return new_total


async def handle_main_conversation(user: User, message: str, db: AsyncSession) -> str:
    import redis as redis_lib
    from app.config import get_settings

    s = get_settings()
    r = redis_lib.from_url(s.REDIS_URL, decode_responses=True)

    await _check_and_update_streak(user, message, db)

    # Hard cap: block before hitting Bedrock at all
    current_tokens = _get_token_usage(user.contact_id, r)
    if current_tokens >= s.BEDROCK_DAILY_TOKEN_HARD_CAP:
        logger.warning("User %s hit daily hard token cap (%d)", user.contact_id, current_tokens)
        from app.services.alerting import send_admin_alert
        send_admin_alert(
            subject=f"[Remy] Daily token hard cap hit for user",
            message=(
                f"User {user.contact_id} has used {current_tokens} tokens today, "
                f"exceeding the hard cap of {s.BEDROCK_DAILY_TOKEN_HARD_CAP}. "
                "Bedrock calls are blocked until tomorrow."
            ),
        )
        return "I've hit my limit for today — talk to you tomorrow!"

    memory_context = await query_memories(user.contact_id, message, db, top_k=3)
    system_prompt = build_system_prompt(user, memory_context)

    # Soft cap: nudge Claude to be brief
    if current_tokens >= s.BEDROCK_DAILY_TOKEN_SOFT_CAP:
        system_prompt += "\n\nIMPORTANT: Token budget is nearly exhausted. Reply in 1 sentence only."
        logger.info("User %s near soft token cap (%d), brevity nudge applied", user.contact_id, current_tokens)

    history = _get_conv_history(user.contact_id, r)
    messages = history + [{"role": "user", "content": message}]

    reply_text = "I hit a snag. Give me a second and try again?"

    # Persists across all agentic turns so a second Bedrock call can't re-fire add_reminder.
    _singleton_tools_called: set[str] = set()
    _SINGLETON_TOOLS = {"add_reminder", "get_google_auth_link"}

    for iteration in range(5):
        logger.info("Agentic loop iter %d — calling Bedrock", iteration)
        response, tokens_used = await call_claude_with_tools(messages, system_prompt)
        _increment_token_usage(user.contact_id, tokens_used, r)
        logger.info(
            "Agentic loop iter %d — stop_reason=%s tokens=%d",
            iteration, response.get("stop_reason"), tokens_used,
        )

        if response.get("stop_reason") == "end_turn":
            reply_text = _extract_text_content(response)
            break

        if response.get("stop_reason") == "tool_use":
            messages.append({"role": "assistant", "content": response["content"]})

            tool_results = []
            for block in response["content"]:
                if block.get("type") == "tool_use":
                    tool_name = block["name"]
                    if tool_name in _SINGLETON_TOOLS and tool_name in _singleton_tools_called:
                        logger.warning("Duplicate %s call in same turn suppressed", tool_name)
                        result = {"status": "already_scheduled"}
                    else:
                        if tool_name in _SINGLETON_TOOLS:
                            _singleton_tools_called.add(tool_name)
                        result = await execute_tool(tool_name, block["input"], user, db)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": json.dumps(result),
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            reply_text = _extract_text_content(response)
            break

    _append_conv_history(user.contact_id, message, reply_text, r)
    return reply_text


_STREAK_MILESTONES: dict[int, str] = {
    7:   "7 days straight. that's a full week — most people don't make it here. keep going.",
    30:  "30 days. a whole month of showing up. that's not luck, that's who you are now.",
    100: "100 days. that's elite. seriously — take a second to recognize what you built.",
}


async def _check_and_update_streak(user: User, message: str, db: AsyncSession) -> None:
    if not is_affirmative(message):
        return

    import redis as redis_lib
    from datetime import date
    from app.config import get_settings

    r = redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)

    today = date.today().isoformat()
    redis_key = f"nightly_sent:{user.contact_id}:{today}"

    if r.exists(redis_key):
        user.streak_count += 1
        await db.commit()
        r.delete(redis_key)

        celebration = _STREAK_MILESTONES.get(user.streak_count)
        if celebration:
            await _send_reply(user.contact_id, celebration)


async def _send_reply(phone: str, message: str) -> None:
    from app.config import get_settings
    if get_settings().PHOTON_ENABLED:
        from app.services.photon_sender import send_via_photon
        await send_via_photon(phone, message)
    else:
        await _send_sms_aws(phone, message)


async def handle_incoming_sms(phone: str, message: str, db: AsyncSession) -> None:
    user, _ = await get_or_create_user(phone, db)

    msg_lower = message.lower().strip()
    if any(kw in msg_lower for kw in _PAUSE_KEYWORDS):
        user.is_paused = True
        await db.commit()
        await _send_reply(user.contact_id, "Got it, going quiet. Text 'resume' whenever you want me back.")
        return
    if any(kw in msg_lower for kw in _RESUME_KEYWORDS):
        user.is_paused = False
        await db.commit()
        await _send_reply(user.contact_id, "I'm back. Good to hear from you.")
        return

    if user.is_paused:
        return

    if user.onboarding_step < 6:
        reply = await handle_onboarding(user, message, db)
    else:
        reply = await handle_main_conversation(user, message, db)

    await _send_reply(user.contact_id, reply)
