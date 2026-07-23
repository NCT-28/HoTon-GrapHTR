from datetime import datetime, timedelta, timezone

from app.dashboard.usage_store import SqliteUsageStore


def _store(tmp_path):
    return SqliteUsageStore(str(tmp_path / "usage.sqlite"))


def test_sqlite_usage_store_ping_returns_true(tmp_path):
    assert _store(tmp_path).ping() is True


def test_sqlite_usage_store_counts_by_tool(tmp_path):
    store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 10.0, "created_at": now,
    })
    store.record({
        "tool_name": "retrieve_chunks", "user_id": "u2", "repo_id": None,
        "success": False, "error_message": "boom", "duration_ms": 20.0, "created_at": now,
    })

    rows = store.counts_by_tool(since=now - timedelta(hours=1))

    assert rows == [{"tool_name": "retrieve_chunks", "calls": 2, "errors": 1, "p50_ms": 20.0}]


def test_sqlite_usage_store_counts_by_tool_excludes_events_before_since(tmp_path):
    store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 10.0, "created_at": now - timedelta(days=2),
    })

    rows = store.counts_by_tool(since=now - timedelta(hours=1))

    assert rows == []


def test_sqlite_usage_store_counts_by_user(tmp_path):
    store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    store.record({
        "tool_name": "get_rag_context", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })
    store.record({
        "tool_name": "query_code_graph", "user_id": "u1", "repo_id": "r1",
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })

    rows = store.counts_by_user(since=now - timedelta(hours=1))

    assert rows == [{"user_id": "u1", "calls": 2}]


def test_sqlite_usage_store_persists_across_reconnect(tmp_path):
    db_path = str(tmp_path / "usage.sqlite")
    now = datetime.now(timezone.utc)
    SqliteUsageStore(db_path).record({
        "tool_name": "embed_text", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 1.0, "created_at": now,
    })

    rows = SqliteUsageStore(db_path).counts_by_tool(since=now - timedelta(hours=1))

    assert rows == [{"tool_name": "embed_text", "calls": 1, "errors": 0, "p50_ms": 1.0}]
