from datetime import datetime, timedelta, timezone


def test_fake_usage_store_counts_by_tool(usage_store):
    now = datetime.now(timezone.utc)
    usage_store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 10.0, "created_at": now,
    })
    usage_store.record({
        "tool_name": "retrieve_chunks", "user_id": "u2", "repo_id": None,
        "success": False, "error_message": "boom", "duration_ms": 20.0, "created_at": now,
    })

    rows = usage_store.counts_by_tool(since=now - timedelta(hours=1))

    assert rows == [{"tool_name": "retrieve_chunks", "calls": 2, "errors": 1, "p50_ms": 20.0}]


def test_fake_usage_store_counts_by_tool_excludes_events_before_since(usage_store):
    now = datetime.now(timezone.utc)
    usage_store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 10.0, "created_at": now - timedelta(days=2),
    })

    rows = usage_store.counts_by_tool(since=now - timedelta(hours=1))

    assert rows == []


def test_fake_usage_store_counts_by_user(usage_store):
    now = datetime.now(timezone.utc)
    usage_store.record({
        "tool_name": "get_rag_context", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })
    usage_store.record({
        "tool_name": "query_code_graph", "user_id": "u1", "repo_id": "r1",
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })

    rows = usage_store.counts_by_user(since=now - timedelta(hours=1))

    assert rows == [{"user_id": "u1", "calls": 2}]
