import asyncio
import json
from typing import Optional

import boto3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.memory import Memory
from app.schemas.memory import MemoryQueryResult

_embedding_client = None


def _get_embedding_client():
    global _embedding_client
    if _embedding_client is None:
        s = get_settings()
        _embedding_client = boto3.client(
            "bedrock-runtime",
            region_name=s.AWS_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
        )
    return _embedding_client


def _get_embedding_sync(text: str) -> list[float]:
    s = get_settings()
    client = _get_embedding_client()
    response = client.invoke_model(
        modelId=s.BEDROCK_EMBEDDING_MODEL_ID,
        body=json.dumps({"inputText": text}),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(response["body"].read())
    return body["embedding"]


async def get_embedding(text: str) -> list[float]:
    return await asyncio.to_thread(_get_embedding_sync, text)


_DEDUP_THRESHOLD = 0.92


async def store_memory(
    phone: str,
    category: str,
    memory_text: str,
    db: AsyncSession,
) -> Memory | None:
    embedding = await get_embedding(memory_text)

    existing = await query_memories(phone, memory_text, db, category=category, top_k=1)
    if existing and existing[0].similarity >= _DEDUP_THRESHOLD:
        return None

    memory = Memory(
        user_phone=phone,
        category=category,
        memory_text=memory_text,
        embedding=embedding,
    )
    db.add(memory)
    await db.commit()
    return memory


async def query_memories(
    phone: str,
    query_text: str,
    db: AsyncSession,
    category: Optional[str] = None,
    top_k: int = 5,
) -> list[MemoryQueryResult]:
    query_embedding = await get_embedding(query_text)

    distance_col = Memory.embedding.cosine_distance(query_embedding).label("distance")

    stmt = (
        select(Memory.memory_text, Memory.category, Memory.created_at, distance_col)
        .where(Memory.user_phone == phone)
        .order_by(distance_col)
        .limit(top_k)
    )

    if category:
        stmt = stmt.where(Memory.category == category)

    result = await db.execute(stmt)
    rows = result.fetchall()

    return [
        MemoryQueryResult(
            memory_text=row.memory_text,
            category=row.category,
            similarity=max(0.0, 1.0 - row.distance),
            created_at=row.created_at,
        )
        for row in rows
    ]
