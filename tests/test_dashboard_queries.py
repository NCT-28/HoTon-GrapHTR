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


def test_tool_usage_delegates_to_usage_store(usage_store):
    now = datetime.now(timezone.utc)
    usage_store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })

    rows = queries.tool_usage(usage_store)

    assert rows == [{"tool_name": "retrieve_chunks", "calls": 1, "errors": 0, "p50_ms": 5.0}]


def test_tool_usage_empty_when_usage_store_is_none():
    assert queries.tool_usage(None) == []


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
