from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

_OBJECTIVE_MAP = {
    "1": "study_buddy",
    "2": "habit_architect",
    "3": "idea_vault",
    "4": "hybrid",
    "study": "study_buddy",
    "study buddy": "study_buddy",
    "habit": "habit_architect",
    "habit architect": "habit_architect",
    "idea": "idea_vault",
    "idea vault": "idea_vault",
    "hybrid": "hybrid",
}

_PERSONA_MAP = {
    "1": "chill_coach",
    "2": "no_bs_peer",
    "3": "drill_sergeant",
    "chill": "chill_coach",
    "chill coach": "chill_coach",
    "no-bs": "no_bs_peer",
    "no bs": "no_bs_peer",
    "drill": "drill_sergeant",
    "drill sergeant": "drill_sergeant",
}

_TZ_ALIASES = {
    "est": "America/New_York",
    "cst": "America/Chicago",
    "mst": "America/Denver",
    "pst": "America/Los_Angeles",
    "eastern": "America/New_York",
    "central": "America/Chicago",
    "mountain": "America/Denver",
    "pacific": "America/Los_Angeles",
    "edt": "America/New_York",
    "cdt": "America/Chicago",
    "mdt": "America/Denver",
    "pdt": "America/Los_Angeles",
}

_GOAL_PROMPTS = {
    "study_buddy": "What's the one academic goal you're chasing this semester?",
    "habit_architect": "What's the core habit you want to lock in?",
    "idea_vault": "What kind of ideas do you want to capture and develop?",
    "hybrid": "Describe your main objective in one sentence.",
}


def _parse_objective(text: str) -> str | None:
    return _OBJECTIVE_MAP.get(text.strip().lower())


def _parse_persona(text: str) -> str | None:
    return _PERSONA_MAP.get(text.strip().lower())


def _parse_timezone(text: str) -> str | None:
    normalized = text.strip()
    alias_lookup = normalized.lower()
    if alias_lookup in _TZ_ALIASES:
        normalized = _TZ_ALIASES[alias_lookup]
    try:
        ZoneInfo(normalized)
        return normalized
    except (ZoneInfoNotFoundError, KeyError):
        return None


async def handle_onboarding(user: User, message: str, db: AsyncSession) -> str:
    step = user.onboarding_step
    reply = ""

    if step == 0:
        user.name = message.strip()[:100]
        user.onboarding_step = 1
        reply = (
            f"Nice to meet you, {user.name}! What's our main mission?\n"
            "1. Study Buddy\n2. Habit Architect\n3. Idea Vault\n4. Hybrid"
        )

    elif step == 1:
        objective = _parse_objective(message)
        if not objective:
            return "Please reply 1, 2, 3, or 4 to choose your mission."
        user.objective = objective
        user.onboarding_step = 2
        reply = _GOAL_PROMPTS[objective]

    elif step == 2:
        user.core_goal = message.strip()[:500]
        user.onboarding_step = 3
        reply = "Got it. Last couple things — pick my vibe:\n1. Chill Coach\n2. No-BS Peer\n3. Drill Sergeant"

    elif step == 3:
        persona = _parse_persona(message)
        if not persona:
            return "Please reply 1, 2, or 3 to choose my style."
        user.persona_style = persona
        user.onboarding_step = 4
        reply = "Almost done! What's your timezone? (e.g., America/New_York, US/Pacific, or just EST)"

    elif step == 4:
        tz = _parse_timezone(message)
        if not tz:
            return (
                "I didn't recognize that timezone. Try 'America/Chicago' or 'US/Eastern' format, "
                "or abbreviations like EST, PST."
            )
        user.timezone = tz
        user.onboarding_step = 5

        from app.tasks.nightly import schedule_first_nightly
        schedule_first_nightly.delay(user.phone_number)

        reply = (
            f"You're all set, {user.name}! I'll check in with you every night at 9 PM {tz}. "
            "Let's get to work."
        )

    await db.commit()
    return reply
