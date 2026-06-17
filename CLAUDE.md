# Arlo / Remy: Life OS AI Assistant

## System Architecture Overview
Arlo/Remy is an asynchronous, text-based AI accountability and lifestyle companion. It acts as an event-driven system routing incoming SMS intents to background tasks, database mutations, or vector lookups.

- **Framework:** FastAPI (Python 3.11+)
- **LLM/Inference:** Amazon Bedrock (Anthropic Claude 3.5 Sonnet) via `boto3`
- **SMS Gateway:** AWS End User Messaging (via Amazon SNS Webhook)
- **Primary Database:** Amazon RDS PostgreSQL (with `pgvector` extension)
- **Task Queue & Cron:** Celery + Amazon ElastiCache (Redis)

---

## Coding Standards & Best Practices

### 1. Asynchronous Execution
- All API endpoint routes in `main.py` MUST be defined using `async def`.
- Use non-blocking libraries (`httpx` for HTTP, `asyncpg` or async SQLAlchemy sessions) for I/O bound tasks.
- Never block the main FastAPI loop with heavy synchronous tasks or long-running computations. Delegate them to Celery.

### 2. Strict Schema Validation (The JSONB Contract)
- The `Events` table uses a flexible `JSONB` column to capture varied Life OS requests (Fitness, Tasks, Reminders).
- **Rule:** You MUST NOT insert data directly into the `JSONB` payload without passing it through a dedicated Pydantic validation schema first.
- Maintain separate, strict Pydantic schemas for:
  - `FitnessLogPayload` (e.g., protein, water, workouts)
  - `TaskPayload` (e.g., description, deadline, status)
  - `ReminderPayload` (e.g., message, execution_timestamp)

### 3. Vector Memory Rules (RAG)
- Long-term memory is segmented inside `pgvector` using strict metadata partitions.
- Every vector entry must include an explicit `category` tag (`academics`, `fitness`, `ideas`, `general`).
- When querying memory, always filter by the relevant `category` tag to minimize token noise and avoid context bleeding.

---

## AI Persona & Guardrails

### 1. Identify and Vibe
- The assistant introduces itself as **Arlo** or **Remy** (depending on final configuration).
- The tone is a blend of an emotionally intelligent, supportive peer and a high-standard motivator. Keep responses concise (under 3 sentences) to match standard SMS norms.

### 2. The Socratic Academic Guardrail
- **Strict Prohibition:** You are an accountability partner, NOT a homework machine or code generator. 
- If the user asks to solve a problem, write an essay, or generate code blocks, you MUST:
  1. Refuse politely but firmly.
  2. Identify the core underlying concept they are grappling with.
  3. Respond with a single guiding, foundational question to help them work it out themselves.

---

## Production CLI Commands

### Environment Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
