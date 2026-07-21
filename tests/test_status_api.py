import uuid

from app.main import create_app
from fastapi.testclient import TestClient


class FakeEmbedder:
    dim = 384

    def embed_batch(self, texts):
        return [[0.1] * 384 for _ in texts]


class FakeLLM:
    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        return "[]"


def test_status_reports_zero_counts_for_new_user(qdrant):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM())
    client = TestClient(app)
    resp = client.get("/api/status", headers={"X-User-Id": str(uuid.uuid4())})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"embed_model_loaded": True, "doc_count": 0, "memory_count": 0}
