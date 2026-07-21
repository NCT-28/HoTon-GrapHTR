import datetime
import uuid

from qdrant_client.models import PointStruct

from app.memory import retrieve_memories
from app.qdrant_store import USER_MEMORIES


class FixedEmbedder:
    def __init__(self, vector):
        self._vector = vector

    def embed_single(self, text):
        return self._vector


def _insert_memory(qdrant, user_id, content, vector, confidence, last_used_at, status="active"):
    qdrant.upsert(
        collection_name=USER_MEMORIES,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "user_id": str(user_id),
                    "content": content,
                    "memory_type": "fact",
                    "confidence": confidence,
                    "source": "inferred",
                    "claim_class": "fact",
                    "status": status,
                    "last_used_at": last_used_at,
                    "created_at": last_used_at,
                },
            )
        ],
        wait=True,
    )


def test_retrieve_memories_returns_fresh_high_confidence(qdrant):
    user_id = uuid.uuid4()
    vector = [1.0] + [0.0] * 383
    now_iso = datetime.datetime.utcnow().isoformat()

    _insert_memory(qdrant, user_id, "fresh memory", vector, confidence=0.9, last_used_at=now_iso)

    results = retrieve_memories(qdrant, FixedEmbedder(vector), user_id, "query", top_k=5, min_similarity=0.5)
    assert len(results) == 1
    assert results[0].content == "fresh memory"


def test_retrieve_memories_decays_old_low_confidence_below_floor(qdrant):
    user_id = uuid.uuid4()
    vector = [1.0] + [0.0] * 383
    old = (datetime.datetime.utcnow() - datetime.timedelta(days=200)).isoformat()

    # confidence 0.35 decayed over 200 days at lambda=0.01 drops well under the 0.3 floor
    _insert_memory(qdrant, user_id, "stale memory", vector, confidence=0.35, last_used_at=old)

    results = retrieve_memories(qdrant, FixedEmbedder(vector), user_id, "query", top_k=5, min_similarity=0.5)
    assert results == []


def test_retrieve_memories_ignores_deprecated(qdrant):
    user_id = uuid.uuid4()
    vector = [1.0] + [0.0] * 383
    now_iso = datetime.datetime.utcnow().isoformat()

    _insert_memory(qdrant, user_id, "deprecated memory", vector, confidence=0.9, last_used_at=now_iso, status="deprecated")

    results = retrieve_memories(qdrant, FixedEmbedder(vector), user_id, "query", top_k=5, min_similarity=0.5)
    assert results == []


def test_retrieve_memories_touches_last_used_at(qdrant):
    user_id = uuid.uuid4()
    vector = [1.0] + [0.0] * 383
    old_iso = (datetime.datetime.utcnow() - datetime.timedelta(days=5)).isoformat()

    _insert_memory(qdrant, user_id, "touched memory", vector, confidence=0.9, last_used_at=old_iso)
    retrieve_memories(qdrant, FixedEmbedder(vector), user_id, "query", top_k=5, min_similarity=0.5)

    points, _ = qdrant.scroll(collection_name=USER_MEMORIES, limit=10)
    updated_last_used = points[0].payload["last_used_at"]
    assert updated_last_used != old_iso
