from unittest.mock import patch

from app.mcp_server import ToolContext, ingest_codebase_impl, query_code_graph_impl
from app.repo_watcher import RepoWatcherManager


def _ctx(graph_store):
    return ToolContext(
        client=None, embedder=None, llm=None, web_search_fn=None,
        graph_store=graph_store, watcher_manager=RepoWatcherManager(graph_store, debounce_seconds=999),
    )


def test_ingest_codebase_parses_and_starts_watching(tmp_path, graph_store):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    ctx = _ctx(graph_store)

    with patch("app.mcp_server.resolve_repo_source", return_value=str(tmp_path)):
        ctx_result = ingest_codebase_impl(ctx, "user-1", str(tmp_path))

    assert ctx_result.symbol_count >= 1
    assert (("user-1", ctx_result.repo_id)) in ctx.watcher_manager.watched_repos()
    ctx.watcher_manager.stop()


def test_query_code_graph_query_mode_returns_matching_subgraph(graph_store):
    graph_store.upsert_symbols([
        {"id": "1", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    ctx = _ctx(graph_store)

    result = query_code_graph_impl(ctx, "u1", "r1", "query", keyword="foo", depth=1)

    assert len(result.nodes) == 1
    assert result.nodes[0].name == "foo"
    ctx.watcher_manager.stop()


def test_query_code_graph_unknown_mode_raises(graph_store):
    ctx = _ctx(graph_store)
    try:
        query_code_graph_impl(ctx, "u1", "r1", "bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass
    ctx.watcher_manager.stop()
