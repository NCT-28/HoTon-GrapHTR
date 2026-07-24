from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Embedder:
    def embed_single(self, text):
        return [0.1]


def _make_client(qdrant, graph_store, usage_store, monkeypatch, user="admin", password="secret"):
    monkeypatch.setenv("DASHBOARD_USER", user)
    monkeypatch.setenv("DASHBOARD_PASSWORD", password)
    from app.config import get_settings
    get_settings.cache_clear()

    from app.dashboard.router import build_dashboard_router

    app = FastAPI()
    router = build_dashboard_router(
        get_client=lambda: qdrant, get_graph_store=lambda: graph_store,
        get_usage_store=lambda: usage_store, get_embedder=lambda: _Embedder(),
    )
    app.include_router(router)
    return TestClient(app)


def test_dashboard_requires_auth_header(qdrant, graph_store, usage_store, monkeypatch):
    client = _make_client(qdrant, graph_store, usage_store, monkeypatch)
    resp = client.get("/dashboard")
    assert resp.status_code == 401


def test_dashboard_rejects_wrong_credentials(qdrant, graph_store, usage_store, monkeypatch):
    client = _make_client(qdrant, graph_store, usage_store, monkeypatch)
    resp = client.get("/dashboard", auth=("admin", "wrong"))
    assert resp.status_code == 401


def test_dashboard_accepts_correct_credentials(qdrant, graph_store, usage_store, monkeypatch):
    client = _make_client(qdrant, graph_store, usage_store, monkeypatch)
    resp = client.get("/dashboard", auth=("admin", "secret"))
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_dashboard_serves_unauthenticated_when_auth_env_unset(qdrant, graph_store, usage_store, monkeypatch):
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()

    from app.dashboard.router import build_dashboard_router

    app = FastAPI()
    router = build_dashboard_router(
        get_client=lambda: qdrant, get_graph_store=lambda: graph_store,
        get_usage_store=lambda: usage_store, get_embedder=lambda: _Embedder(),
    )
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_summary_endpoint_returns_all_six_sections(qdrant, graph_store, usage_store, monkeypatch):
    client = _make_client(qdrant, graph_store, usage_store, monkeypatch)
    resp = client.get("/api/dashboard/summary", auth=("admin", "secret"))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"health", "storage", "tool_usage", "by_project", "by_user"}
    assert len(body["health"]) == 4
    assert len(body["storage"]) == 6
