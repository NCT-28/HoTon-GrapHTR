import uuid

from qdrant_client.models import PointStruct

from app.rag.memory import extract_and_store_memories
from app.clients.qdrant_store import USER_MEMORIES


class FakeEmbedder:
    def embed_single(self, text):
        return [0.9] * 384


class FakeLLM:
    def __init__(self, response_text):
        self._response_text = response_text

    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        return self._response_text


def test_extract_and_store_memories_saves_facts(qdrant):
    user_id = uuid.uuid4()
    llm = FakeLLM('[{"content": "User prefers Python", "type": "preference", "confidence": 0.8}]')

    stored = extract_and_store_memories(
        qdrant, FakeEmbedder(), llm, user_id, "I prefer Python", "Noted, I'll use Python examples."
    )

    assert stored == 1
    points, _ = qdrant.scroll(collection_name=USER_MEMORIES, limit=10)
    assert len(points) == 1
    assert points[0].payload["content"] == "User prefers Python"
    assert points[0].payload["user_id"] == str(user_id)
    assert points[0].payload["status"] == "active"


def test_extract_and_store_memories_empty_array_stores_nothing(qdrant):
    user_id = uuid.uuid4()
    llm = FakeLLM("[]")
    stored = extract_and_store_memories(qdrant, FakeEmbedder(), llm, user_id, "hi", "hello")
    assert stored == 0


def test_correction_deprecates_conflicting_memory(qdrant):
    user_id = uuid.uuid4()
    vector = [0.9] * 384

    # Seed an existing active memory with a near-identical embedding.
    qdrant.upsert(
        collection_name=USER_MEMORIES,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "user_id": str(user_id),
                    "content": "User works with Java",
                    "memory_type": "fact",
                    "confidence": 0.7,
                    "source": "inferred",
                    "claim_class": "fact",
                    "status": "active",
                    "last_used_at": "2020-01-01T00:00:00",
                    "created_at": "2020-01-01T00:00:00",
                },
            )
        ],
        wait=True,
    )

    llm = FakeLLM('[{"content": "User actually works with Kotlin now", "type": "correction", "confidence": 0.9}]')
    stored = extract_and_store_memories(qdrant, FakeEmbedder(), llm, user_id, "correction", "noted")

    assert stored == 1
    points, _ = qdrant.scroll(collection_name=USER_MEMORIES, limit=10)
    statuses = {p.payload["content"]: p.payload["status"] for p in points}
    assert statuses["User works with Java"] == "deprecated"
    assert statuses["User actually works with Kotlin now"] == "active"
