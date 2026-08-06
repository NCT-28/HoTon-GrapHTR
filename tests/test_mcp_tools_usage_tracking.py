from app.mcp_server import ToolContext, ingest_codebase_impl
from app.dashboard.tracker import track_usage


def test_ingest_codebase_impl_records_usage(tmp_path, usage_store):
    (tmp_path / "mod.py").write_text("def helper():\n    return 1\n")
    ctx = ToolContext(client=None, embedder=None, llm=None, web_search_fn=None)

    with track_usage(usage_store, "ingest_codebase", ""):
        ingest_codebase_impl(ctx, str(tmp_path))

    assert usage_store.events[0]["tool_name"] == "ingest_codebase"
    assert usage_store.events[0]["user_id"] == ""
    assert usage_store.events[0]["success"] is True
