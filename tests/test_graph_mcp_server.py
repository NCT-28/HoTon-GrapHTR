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


def test_ingest_codebase_tool_is_registered_on_the_built_app():
    # Regression coverage for the @mcp.tool() wiring itself: drive the real MCP
    # streamable-http transport (initialize -> tools/list) against the app built
    # by create_graph_only_app(), rather than calling the impl function directly.
    # This fails if the @mcp.tool() decorator is ever removed from
    # app/graph_mcp_server.py, even though test_ingest_codebase_tool_runs_against_
    # fixture_repo above would not notice.
    app = create_graph_only_app()
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app) as client:
        init_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.1"},
                },
            },
            headers=headers,
        )
        assert init_resp.status_code == 200

        list_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert list_resp.status_code == 200
        tool_names = {tool["name"] for tool in list_resp.json()["result"]["tools"]}
        assert "ingest_codebase" in tool_names


def test_ingest_codebase_tool_runs_against_fixture_repo(tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")

    from app.graph.ingest import ingest_codebase_impl

    result = ingest_codebase_impl(str(tmp_path))

    out_dir = tmp_path / "graphtr-out"
    assert result.symbol_count > 0
    assert (out_dir / "graph.json").exists()
    graph = json.loads((out_dir / "graph.json").read_text())
    assert len(graph["nodes"]) == result.symbol_count
