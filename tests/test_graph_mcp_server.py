import json

from fastapi.testclient import TestClient

from app.graph_mcp_server import create_graph_only_app


def test_health_returns_graph_only_mode():
    app = create_graph_only_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "mode": "graph-only"}


def test_mcp_endpoint_is_mounted():
    app = create_graph_only_app()
    with TestClient(app) as client:
        resp = client.post("/mcp", json={})
        assert resp.status_code != 404


def test_ingest_codebase_tool_runs_against_fixture_repo(tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")

    from app.graph.ingest import ingest_codebase_impl

    result = ingest_codebase_impl(str(tmp_path))

    out_dir = tmp_path / "graphtr-out"
    assert result.symbol_count > 0
    assert (out_dir / "graph.json").exists()
    graph = json.loads((out_dir / "graph.json").read_text())
    assert len(graph["nodes"]) == result.symbol_count
