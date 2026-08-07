from fastapi.testclient import TestClient

from app.main import create_app


class FakeEmbedder:
    dim = 384

    def embed_batch(self, texts):
        return [[0.1] * 384 for _ in texts]

    def embed_single(self, text):
        return [0.1] * 384


def test_mcp_endpoint_is_mounted(qdrant):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder())
    with TestClient(app) as client:
        # A GET on the MCP endpoint without a proper MCP session should not 404 —
        # it's a real mounted route (exact response shape depends on the MCP SDK,
        # so we only assert routing succeeded, not protocol details).
        resp = client.post("/mcp", json={})
        assert resp.status_code != 404


def test_health_still_works_alongside_mcp(qdrant):
    app = create_app(qdrant_client=qdrant, embedder=FakeEmbedder())
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200


def test_tool_context_has_no_watcher_manager():
    import dataclasses

    from app.mcp_server import ToolContext

    fields = {f.name for f in dataclasses.fields(ToolContext)}
    assert "watcher_manager" not in fields
    assert "llm" not in fields
    assert "graph_store" not in fields
    assert {"client", "embedder", "usage_store"} <= fields
