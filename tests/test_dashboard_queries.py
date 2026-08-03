import os
from datetime import datetime, timezone

from app.dashboard import queries


def test_storage_breakdown_counts_all_six_collections(qdrant):
    rows = queries.storage_breakdown(qdrant)
    names = {r["collection"] for r in rows}
    assert names == {
        "rag_documents", "rag_chunks", "user_memories",
        "user_profiles", "profile_snapshots", "code_symbol_embeddings",
    }
    assert all(r["points"] == 0 for r in rows)  # fresh in-memory qdrant
    assert all(r["percent"] == 0.0 for r in rows)  # 0 / 0 total must not raise ZeroDivisionError


def test_storage_breakdown_percent_is_share_of_total_points(qdrant):
    import uuid

    from qdrant_client.models import PointStruct

    qdrant.upsert(
        collection_name="rag_chunks",
        points=[PointStruct(id=str(uuid.uuid4()), vector=[0.0] * 384, payload={}) for _ in range(3)],
        wait=True,
    )
    qdrant.upsert(
        collection_name="user_memories",
        points=[PointStruct(id=str(uuid.uuid4()), vector=[0.0] * 384, payload={})],
        wait=True,
    )

    rows = queries.storage_breakdown(qdrant)

    by_collection = {r["collection"]: r for r in rows}
    assert by_collection["rag_chunks"]["percent"] == 75.0
    assert by_collection["user_memories"]["percent"] == 25.0
    assert by_collection["rag_documents"]["percent"] == 0.0


def _upsert_code_symbol_points(client, count: int, repo_id: str = "r1") -> None:
    import uuid

    from qdrant_client.models import PointStruct

    from app.clients.qdrant_store import CODE_SYMBOL_EMBEDDINGS

    client.upsert(
        collection_name=CODE_SYMBOL_EMBEDDINGS,
        points=[
            PointStruct(id=str(uuid.uuid4()), vector=[0.0] * 384, payload={"repo_id": repo_id})
            for _ in range(count)
        ],
        wait=True,
    )


def test_storage_breakdown_fans_out_code_symbol_embeddings_across_repo_dirs_in_local_mode(
    tmp_path, monkeypatch, graph_store, qdrant,
):
    from app.clients.qdrant_store import get_repo_qdrant_client
    from app.config import get_settings

    monkeypatch.setenv("DEPLOY_MODE", "local")
    get_settings.cache_clear()

    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    repo1.mkdir()
    repo2.mkdir()
    graph_store.upsert_repo({
        "user_id": "u1", "repo_id": "r1", "source": str(repo1),
        "local_path": str(repo1), "last_indexed_at": "t1",
    })
    graph_store.upsert_repo({
        "user_id": "u1", "repo_id": "r2", "source": str(repo2),
        "local_path": str(repo2), "last_indexed_at": "t1",
    })
    _upsert_code_symbol_points(get_repo_qdrant_client(str(repo1)), 2, "r1")
    _upsert_code_symbol_points(get_repo_qdrant_client(str(repo2)), 3, "r2")
    # points on the shared/central client must NOT be counted -- code-symbol vectors
    # live per-repo in local mode, this client is unrelated leftover/stale data.
    _upsert_code_symbol_points(qdrant, 99, "stale-central")

    rows = queries.storage_breakdown(qdrant, graph_store)

    by_collection = {r["collection"]: r for r in rows}
    assert by_collection["code_symbol_embeddings"]["points"] == 5

    get_repo_qdrant_client.cache_clear()
    get_settings.cache_clear()


def test_storage_breakdown_skips_repos_whose_local_path_no_longer_exists(monkeypatch, graph_store, qdrant):
    from app.config import get_settings

    monkeypatch.setenv("DEPLOY_MODE", "local")
    get_settings.cache_clear()
    graph_store.upsert_repo({
        "user_id": "u1", "repo_id": "r1", "source": "/does/not/exist",
        "local_path": "/does/not/exist", "last_indexed_at": "t1",
    })

    rows = queries.storage_breakdown(qdrant, graph_store)

    by_collection = {r["collection"]: r for r in rows}
    assert by_collection["code_symbol_embeddings"]["points"] == 0
    assert not os.path.exists("/does/not/exist")

    get_settings.cache_clear()


def test_storage_breakdown_uses_central_client_count_in_server_deploy_mode(monkeypatch, graph_store, qdrant):
    from app.config import get_settings

    monkeypatch.setenv("DEPLOY_MODE", "server")
    get_settings.cache_clear()
    _upsert_code_symbol_points(qdrant, 4, "r1")

    rows = queries.storage_breakdown(qdrant, graph_store)

    by_collection = {r["collection"]: r for r in rows}
    assert by_collection["code_symbol_embeddings"]["points"] == 4

    get_settings.cache_clear()


def test_project_breakdown_empty_when_no_repos(graph_store):
    assert queries.project_breakdown(graph_store) == []


def test_project_breakdown_reports_node_and_edge_counts(graph_store):
    graph_store.upsert_repo({
        "user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
        "local_path": "/tmp/r1", "last_indexed_at": "2026-07-22T00:00:00Z",
    })
    graph_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "s2", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "bar",
         "file_path": "a.py", "start_line": 3, "end_line": 4, "language": "python"},
    ])
    graph_store.upsert_code_edges([{"source": "s1", "target": "s2", "type": "CALLS"}])

    rows = queries.project_breakdown(graph_store)

    assert rows == [{
        "repo_id": "r1", "node_count": 2, "edge_count": 1, "last_indexed_at": "2026-07-22T00:00:00Z",
    }]


def test_project_breakdown_empty_when_graph_store_is_none():
    assert queries.project_breakdown(None) == []


def test_mcp_tool_usage_splits_out_mcp_tools_only(usage_store):
    now = datetime.now(timezone.utc)
    usage_store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })
    usage_store.record({
        "tool_name": "list_documents", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 3.0, "created_at": now,
    })

    rows = queries.mcp_tool_usage(usage_store)

    assert rows == [{"tool_name": "retrieve_chunks", "calls": 1, "errors": 0, "p50_ms": 5.0}]


def test_mcp_tool_usage_empty_when_usage_store_is_none():
    assert queries.mcp_tool_usage(None) == []


def test_route_usage_splits_out_non_mcp_routes_only(usage_store):
    now = datetime.now(timezone.utc)
    usage_store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })
    usage_store.record({
        "tool_name": "list_documents", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 3.0, "created_at": now,
    })

    rows = queries.route_usage(usage_store)

    assert rows == [{"tool_name": "list_documents", "calls": 1, "errors": 0, "p50_ms": 3.0}]


def test_route_usage_empty_when_usage_store_is_none():
    assert queries.route_usage(None) == []


def test_user_breakdown_combines_usage_and_qdrant_counts(qdrant, usage_store):
    import uuid

    from qdrant_client.models import PointStruct

    now = datetime.now(timezone.utc)
    usage_store.record({
        "tool_name": "get_rag_context", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })
    qdrant.upsert(
        collection_name="rag_documents",
        points=[PointStruct(id=str(uuid.uuid4()), vector=[0.0], payload={"user_id": "u1"})],
        wait=True,
    )

    rows = queries.user_breakdown(qdrant, usage_store)

    assert rows == [{"user_id": "u1", "tool_calls": 1, "doc_count": 1, "memory_count": 0}]


def test_user_breakdown_empty_when_usage_store_is_none(qdrant):
    assert queries.user_breakdown(qdrant, None) == []


def test_project_breakdown_does_not_materialize_the_graph(graph_store):
    graph_store.upsert_repo({
        "user_id": "u1", "repo_id": "r1", "source": "s",
        "local_path": "/tmp/r1", "last_indexed_at": "now",
    })
    graph_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    graph_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    calls = {"get_subgraph": 0}
    real_get_subgraph = graph_store.get_subgraph

    def counting_get_subgraph(user_id, repo_id):
        calls["get_subgraph"] += 1
        return real_get_subgraph(user_id, repo_id)

    graph_store.get_subgraph = counting_get_subgraph
    # FakeGraphStore.count_subgraph delegates to self.get_subgraph, which would pick
    # up the instance attribute set on the line above and make the counter fire even
    # on the fixed code. Stub it so the assertion measures project_breakdown only.
    graph_store.count_subgraph = lambda user_id, repo_id: (2, 1)

    result = queries.project_breakdown(graph_store)

    assert result == [
        {"repo_id": "r1", "node_count": 2, "edge_count": 1, "last_indexed_at": "now"}
    ]
    assert calls["get_subgraph"] == 0


class _Point:
    def __init__(self, user_id):
        self.payload = {"user_id": user_id}


def test_count_by_user_id_follows_the_scroll_cursor():
    # The old implementation passed limit=10000 and dropped the returned cursor,
    # so anything past the first page was silently uncounted.
    class _PagedClient:
        def __init__(self):
            self.pages = [
                ([_Point("u1"), _Point("u1")], "cursor-1"),
                ([_Point("u2")], None),
            ]
            self.offsets_seen = []

        def scroll(self, collection_name, limit, with_payload, offset=None):
            self.offsets_seen.append(offset)
            return self.pages.pop(0)

    client = _PagedClient()

    counts = queries._count_by_user_id(client, "rag_documents")

    assert counts == {"u1": 2, "u2": 1}
    assert client.offsets_seen == [None, "cursor-1"]
