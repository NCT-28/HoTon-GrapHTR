"""Memory retrieval and storage."""

import datetime
import math
import uuid as _uuid
from dataclasses import dataclass as _dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from app.clients.qdrant_store import USER_MEMORIES


@_dataclass
class RetrievedMemory:
    id: str
    content: str
    memory_type: str
    confidence: float


def retrieve_memories(
    client: QdrantClient,
    embedder,
    user_id: _uuid.UUID,
    query: str,
    top_k: int,
    min_similarity: float,
) -> list[RetrievedMemory]:
    query_vector = embedder.embed_single(query)

    hits = client.query_points(
        collection_name=USER_MEMORIES,
        query=query_vector,
        query_filter=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=str(user_id))),
                FieldCondition(key="status", match=MatchValue(value="active")),
            ]
        ),
        limit=top_k * 3,
        with_payload=True,
    ).points

    now = datetime.datetime.utcnow()
    scored = []
    for hit in hits:
        if hit.score < min_similarity:
            continue
        payload = hit.payload
        last_used_at = datetime.datetime.fromisoformat(payload["last_used_at"])
        age_days = (now - last_used_at).total_seconds() / 86400.0
        effective_confidence = payload["confidence"] * math.exp(-0.01 * age_days)
        if effective_confidence < 0.3:
            continue
        scored.append((effective_confidence, hit.score, hit.id, payload))

    scored.sort(key=lambda t: (-t[0], -t[1]))
    top = scored[:top_k]

    if top:
        now_iso = now.isoformat()
        for _eff, _score, point_id, _payload in top:
            client.set_payload(collection_name=USER_MEMORIES, payload={"last_used_at": now_iso}, points=[point_id])

    return [
        RetrievedMemory(id=str(point_id), content=payload["content"], memory_type=payload["memory_type"], confidence=eff)
        for eff, _score, point_id, payload in top
    ]


# --- REST layer ---

from fastapi import APIRouter, Header, HTTPException, status
from qdrant_client.models import PointIdsList

from app.dashboard.tracker import track_usage


def list_memories(client: QdrantClient, user_id: _uuid.UUID) -> list[dict]:
    points, _ = client.scroll(
        collection_name=USER_MEMORIES,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=str(user_id))),
                FieldCondition(key="status", match=MatchValue(value="active")),
            ]
        ),
        limit=1000,
    )
    memories = [
        {
            "id": p.id,
            "content": p.payload["content"],
            "memory_type": p.payload["memory_type"],
            "confidence": p.payload["confidence"],
            "created_at": p.payload["created_at"],
        }
        for p in points
    ]
    memories.sort(key=lambda m: (m["confidence"], m["created_at"]), reverse=True)
    return memories


def delete_memory(client: QdrantClient, memory_id: str, user_id: _uuid.UUID) -> bool:
    points = client.retrieve(collection_name=USER_MEMORIES, ids=[memory_id])
    if not points or points[0].payload.get("user_id") != str(user_id):
        return False
    client.delete(collection_name=USER_MEMORIES, points_selector=PointIdsList(points=[memory_id]))
    return True


def build_memory_router(get_client, get_usage_store=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/memories")
    async def get_memories(x_user_id: str = Header(...)):
        with track_usage(get_usage_store() if get_usage_store else None, "get_memories", x_user_id):
            try:
                user_id = _uuid.UUID(x_user_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid X-User-Id header")
            return list_memories(get_client(), user_id)

    @router.delete("/api/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_memory(memory_id: str, x_user_id: str = Header(...)):
        with track_usage(get_usage_store() if get_usage_store else None, "remove_memory", x_user_id):
            try:
                user_id = _uuid.UUID(x_user_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid X-User-Id header")
            if not delete_memory(get_client(), memory_id, user_id):
                raise HTTPException(status_code=404, detail="Memory not found")
            return None

    return router
