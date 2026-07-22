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


def test_mcp_endpoint_is_mounted(qdrant, graph_store):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store)
    with TestClient(app) as client:
        # A GET on the MCP endpoint without a proper MCP session should not 404 —
        # it's a real mounted route (exact response shape depends on the MCP SDK,
        # so we only assert routing succeeded, not protocol details).
        resp = client.post("/mcp", json={})
        assert resp.status_code != 404


def test_health_still_works_alongside_mcp(qdrant, graph_store):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder(), llm=FakeLLM(), graph_store=graph_store)
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
