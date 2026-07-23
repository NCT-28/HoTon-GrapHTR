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


def test_get_usage_store_returns_sqlite_store_in_local_deploy_mode(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.dashboard.usage_store import get_usage_store

    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("USAGE_DB_HOST", raising=False)
    monkeypatch.delenv("USAGE_DB_URL", raising=False)
    get_settings.cache_clear()
    get_usage_store.cache_clear()

    store = get_usage_store()

    assert isinstance(store, SqliteUsageStore)
    assert (tmp_path / "usage.sqlite").exists()

    get_usage_store.cache_clear()
    get_settings.cache_clear()
