import asyncio
import json

import boto3

from app.config import get_settings

_bedrock_client = None

TOOL_DEFINITIONS = [
    {
        "name": "add_reminder",
        "description": (
            "Schedule a future SMS reminder for the user at a specific time. "
            "Use this when the user wants to be reminded about something later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time_str": {
                    "type": "string",
                    "description": "ISO 8601 UTC datetime string for when to send the reminder (e.g., '2025-01-15T22:00:00Z')",
                },
                "message": {
                    "type": "string",
                    "description": "The reminder message to send (max 160 characters)",
                },
            },
            "required": ["time_str", "message"],
        },
    },
    {
        "name": "log_event",
        "description": (
            "Log a fitness, task, or reminder event to the database. "
            "Use for tracking workouts, meals, todos, or completed activities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "enum": ["fitness_log", "task", "reminder"],
                    "description": "The category of event to log",
                },
                "data": {
                    "type": "object",
                    "description": (
                        "Event data matching the event_type schema. "
                        "For fitness_log: {protein_grams, water_liters, workout_type, duration_minutes, notes}. "
                        "For task: {description, deadline, status, priority}. "
                        "For reminder: {message, execution_timestamp}."
                    ),
                },
            },
            "required": ["event_type", "data"],
        },
    },
    {
        "name": "query_schedule",
        "description": "Retrieve events and tasks from the database for a specific date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_str": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format to retrieve events for",
                },
            },
            "required": ["date_str"],
        },
    },
    {
        "name": "store_memory",
        "description": (
            "Store an important fact or memory in long-term vector storage for future recall. "
            "Use this when the user shares something worth remembering long-term."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["academics", "fitness", "ideas", "general"],
                    "description": "Category tag for the memory (must be one of the four options)",
                },
                "memory_text": {
                    "type": "string",
                    "description": "The text to store as a long-term memory",
                },
            },
            "required": ["category", "memory_text"],
        },
    },
    {
        "name": "get_google_auth_link",
        "description": (
            "Generate a Google account connection link and send it to the user. "
            "Use this when the user asks to connect Google, link their calendar, "
            "or enable Gmail integration. Returns a URL to include in your SMS reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "add_calendar_event",
        "description": (
            "Add an event to the user's primary Google Calendar. "
            "Use this when the user asks to schedule, block time, or create a calendar event. "
            "Resolve relative times ('tomorrow', 'next Monday') using CURRENT UTC TIME in your system prompt. "
            "Always include the user's local timezone offset in the ISO 8601 strings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Event title shown on the calendar",
                },
                "start_time_iso": {
                    "type": "string",
                    "description": (
                        "ISO 8601 datetime with timezone offset for event start, "
                        "e.g. '2026-07-01T09:00:00-05:00'"
                    ),
                },
                "end_time_iso": {
                    "type": "string",
                    "description": "ISO 8601 datetime with timezone offset for event end",
                },
                "description": {
                    "type": "string",
                    "description": "Optional longer description or notes for the event",
                },
            },
            "required": ["summary", "start_time_iso", "end_time_iso"],
        },
    },
    {
        "name": "send_gmail",
        "description": (
            "Send an email from the user's Gmail account. "
            "Use this when the user asks to email, send a message, or draft and send something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_email": {
                    "type": "string",
                    "description": "Recipient's email address",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body_text": {
                    "type": "string",
                    "description": "Plain-text email body",
                },
            },
            "required": ["to_email", "subject", "body_text"],
        },
    },
]


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        s = get_settings()
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=s.AWS_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
        )
    return _bedrock_client


def _invoke_model_sync(body: dict) -> dict:
    s = get_settings()
    client = _get_bedrock_client()
    response = client.invoke_model(
        modelId=s.BEDROCK_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())


async def call_claude_with_tools(messages: list[dict], system_prompt: str) -> dict:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "system": system_prompt,
        "messages": messages,
        "tools": TOOL_DEFINITIONS,
        "tool_choice": {"type": "auto"},
    }
    return await asyncio.to_thread(_invoke_model_sync, body)
