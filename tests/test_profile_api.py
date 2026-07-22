import uuid

from fastapi.testclient import TestClient

from app.main import create_app


class FakeEmbedder:
    dim = 384

    def embed_batch(self, texts):
        return [[0.1] * 384 for _ in texts]

    def embed_single(self, text):
        return [0.1] * 384


class FakeLLM:
    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        return "[]"


def test_get_profile_returns_default(qdrant, graph_store):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store)
    client = TestClient(app)
    resp = client.get("/api/profile", headers={"X-User-Id": str(uuid.uuid4())})
    assert resp.status_code == 200
    assert resp.json()["level"] == "unknown"


def test_patch_profile_updates_fields(qdrant, graph_store):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store)
    client = TestClient(app)
    user_id = str(uuid.uuid4())

    resp = client.patch(
        "/api/profile",
        json={"level": "advanced", "preferred_lang": "vi"},
        headers={"X-User-Id": user_id},
    )
    assert resp.status_code == 200
    assert resp.json()["level"] == "advanced"
    assert resp.json()["preferred_lang"] == "vi"

    fetched = client.get("/api/profile", headers={"X-User-Id": user_id})
    assert fetched.json()["level"] == "advanced"


def test_get_profile_records_usage(qdrant, graph_store, usage_store):
    app = create_app(
        qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store, usage_store=usage_store,
    )
    client = TestClient(app)
    client.get("/api/profile", headers={"X-User-Id": str(uuid.uuid4())})

    assert any(e["tool_name"] == "get_profile" for e in usage_store.events)
