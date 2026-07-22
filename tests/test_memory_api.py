import uuid

from fastapi.testclient import TestClient
from qdrant_client.models import PointStruct

from app.main import create_app
from app.clients.qdrant_store import USER_MEMORIES


class FakeEmbedder:
    dim = 384

    def embed_batch(self, texts):
        return [[0.1] * 384 for _ in texts]

    def embed_single(self, text):
        return [0.1] * 384


class FakeLLM:
    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        return "[]"


def _seed_memory(qdrant, user_id, content, confidence=0.8):
    qdrant.upsert(
        collection_name=USER_MEMORIES,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.1] * 384,
                payload={
                    "user_id": str(user_id),
                    "content": content,
                    "memory_type": "fact",
                    "confidence": confidence,
                    "source": "inferred",
                    "claim_class": "fact",
                    "status": "active",
                    "last_used_at": "2026-01-01T00:00:00",
                    "created_at": "2026-01-01T00:00:00",
                },
            )
        ],
        wait=True,
    )
    return qdrant.scroll(collection_name=USER_MEMORIES, limit=1)[0][0].id


def test_list_memories_returns_active_only(qdrant, graph_store):
    user_id = uuid.uuid4()
    _seed_memory(qdrant, user_id, "remembered fact")

    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store)
    client = TestClient(app)
    resp = client.get("/api/memories", headers={"X-User-Id": str(user_id)})

    assert resp.status_code == 200
    memories = resp.json()
    assert len(memories) == 1
    assert memories[0]["content"] == "remembered fact"


def test_delete_memory_removes_it(qdrant, graph_store):
    user_id = uuid.uuid4()
    memory_id = _seed_memory(qdrant, user_id, "to be deleted")

    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store)
    client = TestClient(app)
    resp = client.delete(f"/api/memories/{memory_id}", headers={"X-User-Id": str(user_id)})
    assert resp.status_code == 204

    listed = client.get("/api/memories", headers={"X-User-Id": str(user_id)})
    assert listed.json() == []


def test_delete_memory_wrong_user_returns_404(qdrant, graph_store):
    user_id = uuid.uuid4()
    memory_id = _seed_memory(qdrant, user_id, "someone else's memory")

    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store)
    client = TestClient(app)
    resp = client.delete(f"/api/memories/{memory_id}", headers={"X-User-Id": str(uuid.uuid4())})
    assert resp.status_code == 404


def test_get_memories_records_usage(qdrant, graph_store, usage_store):
    app = create_app(
        qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store, usage_store=usage_store,
    )
    client = TestClient(app)
    client.get("/api/memories", headers={"X-User-Id": str(uuid.uuid4())})

    assert any(e["tool_name"] == "get_memories" for e in usage_store.events)
