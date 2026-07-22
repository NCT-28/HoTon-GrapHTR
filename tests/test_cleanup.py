import datetime
import uuid

from qdrant_client.models import PointStruct

from app.rag.cleanup import run_memory_cleanup
from app.clients.qdrant_store import USER_MEMORIES


def _insert(qdrant, status, confidence, last_used_at, content="x"):
    qdrant.upsert(
        collection_name=USER_MEMORIES,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.1] * 384,
                payload={
                    "user_id": str(uuid.uuid4()),
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


def test_deletes_old_deprecated_memories(qdrant):
    old = (datetime.datetime.utcnow() - datetime.timedelta(days=31)).isoformat()
    recent = (datetime.datetime.utcnow() - datetime.timedelta(days=5)).isoformat()
    _insert(qdrant, "deprecated", 0.5, old, content="old deprecated")
    _insert(qdrant, "deprecated", 0.5, recent, content="recent deprecated")

    deleted = run_memory_cleanup(qdrant)

    assert deleted == 1
    remaining, _ = qdrant.scroll(collection_name=USER_MEMORIES, limit=10)
    assert [p.payload["content"] for p in remaining] == ["recent deprecated"]


def test_deletes_old_low_confidence_active_memories(qdrant):
    old = (datetime.datetime.utcnow() - datetime.timedelta(days=15)).isoformat()
    recent = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat()
    _insert(qdrant, "active", 0.1, old, content="stale low-confidence")
    _insert(qdrant, "active", 0.1, recent, content="fresh low-confidence")
    _insert(qdrant, "active", 0.9, old, content="stale high-confidence")

    deleted = run_memory_cleanup(qdrant)

    assert deleted == 1
    remaining, _ = qdrant.scroll(collection_name=USER_MEMORIES, limit=10)
    contents = {p.payload["content"] for p in remaining}
    assert contents == {"fresh low-confidence", "stale high-confidence"}


def test_cleanup_on_empty_collection_returns_zero(qdrant):
    assert run_memory_cleanup(qdrant) == 0
