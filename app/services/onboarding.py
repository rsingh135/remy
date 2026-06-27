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
    "habits": "habit_architect",
    "habit architect": "habit_architect",
    "idea": "idea_vault",
    "ideas": "idea_vault",
    "idea vault": "idea_vault",
    "hybrid": "hybrid",
    "mix": "hybrid",
    "all": "hybrid",
    "all of it": "hybrid",
}

_PERSONA_MAP = {
    "1": "chill_coach",
    "2": "no_bs_peer",
    "3": "drill_sergeant",
    "chill": "chill_coach",
    "chill coach": "chill_coach",
    "supportive": "chill_coach",
    "direct": "no_bs_peer",
    "no-bs": "no_bs_peer",
    "no bs": "no_bs_peer",
    "straight": "no_bs_peer",
    "real": "no_bs_peer",
    "tough": "drill_sergeant",
    "hard": "drill_sergeant",
    "drill": "drill_sergeant",
    "drill sergeant": "drill_sergeant",
    "push": "drill_sergeant",
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
    "study_buddy": "what's the main academic thing you're trying to nail right now?",
    "habit_architect": "what's the one habit you actually want to lock in?",
    "idea_vault": "what kind of ideas do you want me to help you capture?",
    "hybrid": "what's the main thing on your plate right now?",
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
            f"nice {user.name}. what are we mainly working on?\n\n"
            "- study: keeping up with classes, exams, deadlines\n"
            "- habits: building routines you actually stick to\n"
            "- ideas: capturing and developing your thoughts\n"
            "- hybrid: mix of everything\n\n"
            "just text one"
        )

    elif step == 1:
        objective = _parse_objective(message)
        if not objective:
            return "didn't catch that — text study, habits, ideas, or hybrid"
        user.objective = objective
        user.onboarding_step = 2
        reply = _GOAL_PROMPTS[objective]

    elif step == 2:
        user.core_goal = message.strip()[:500]
        user.onboarding_step = 3
        reply = (
            "ok. how do you want me to talk to you?\n\n"
            "- chill: supportive, low pressure, here for the wins\n"
            "- direct: straight talk, no fluff, I'll call you out if needed\n"
            "- tough: high standards, no excuses, I'll push you hard\n\n"
            "text one"
        )

    elif step == 3:
        persona = _parse_persona(message)
        if not persona:
            return "text chill, direct, or tough"
        user.persona_style = persona
        user.onboarding_step = 4
        reply = "last thing — what timezone? (EST, PST, CST, MST all work, or full like America/Chicago)"

    elif step == 4:
        tz = _parse_timezone(message)
        if not tz:
            return "didn't get that timezone. try EST, PST, or something like America/New_York"
        user.timezone = tz
        user.onboarding_step = 5

        from app.tasks.nightly import schedule_first_nightly
        schedule_first_nightly.delay(user.phone_number)

        reply = (
            f"locked in. I'll check in at 9pm your time every night.\n\n"
            "things I can do: set reminders, log workouts and meals, "
            "remember stuff you tell me, keep your streak alive. "
            "just text me like you would a friend.\n\n"
            f"let's get it {user.name}"
        )

    await db.commit()
    return reply
