from app.mcp_server import ToolContext, query_code_graph_impl
from app.dashboard.tracker import track_usage


def test_query_code_graph_impl_records_usage_with_repo_id(graph_store, usage_store):
    graph_store.upsert_symbols([
        {"id": "1", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    ctx = ToolContext(client=None, embedder=None, llm=None, web_search_fn=None, graph_store=graph_store)

    with track_usage(usage_store, "query_code_graph", "u1", repo_id="r1"):
        query_code_graph_impl(ctx, "u1", "r1", "query", keyword="foo", depth=1)

    assert usage_store.events[0]["tool_name"] == "query_code_graph"
    assert usage_store.events[0]["repo_id"] == "r1"
    assert usage_store.events[0]["success"] is True
