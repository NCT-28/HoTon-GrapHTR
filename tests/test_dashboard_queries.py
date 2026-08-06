from datetime import datetime, timezone

from app.dashboard import queries


def test_storage_breakdown_counts_all_five_collections(qdrant):
    rows = queries.storage_breakdown(qdrant)
    names = {r["collection"] for r in rows}
    assert names == {
        "rag_documents", "rag_chunks", "user_memories",
        "user_profiles", "profile_snapshots",
    }
    assert all(r["points"] == 0 for r in rows)  # fresh in-memory qdrant
    assert all(r["percent"] == 0.0 for r in rows)  # 0 / 0 total must not raise ZeroDivisionError


def test_project_breakdown_is_gone():
    assert not hasattr(queries, "project_breakdown")


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
