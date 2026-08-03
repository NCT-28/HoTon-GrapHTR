import threading
import time

from qdrant_client.models import FieldCondition, Filter, MatchValue

from app.clients.qdrant_store import CODE_SYMBOL_EMBEDDINGS
from app.graph.repo_watcher import RepoWatcherManager


def _count_for_repo(qdrant, repo_id: str) -> int:
    return qdrant.count(
        collection_name=CODE_SYMBOL_EMBEDDINGS,
        count_filter=Filter(must=[FieldCondition(key="repo_id", match=MatchValue(value=repo_id))]),
    ).count


class _FakeEmbedder:
    def embed_batch(self, texts):
        return [[float(len(t))] * 384 for t in texts]


class _SlowFakeEmbedder:
    """Sleeps mid-call so two reindex() calls can be made to overlap in a test."""

    def __init__(self, delay: float):
        self._delay = delay

    def embed_batch(self, texts):
        time.sleep(self._delay)
        return [[float(len(t))] * 384 for t in texts]


def test_reindex_populates_graph_store_from_repo(tmp_path, graph_store):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    manager = RepoWatcherManager(graph_store)

    manager.reindex("user-1", "repo-1", str(tmp_path))

    nodes, _ = graph_store.get_subgraph("user-1", "repo-1")
    assert any(n["name"] == "foo" for n in nodes)
    assert graph_store.get_repo("user-1", "repo-1")["local_path"] == str(tmp_path)


def test_watch_reindexes_automatically_on_file_change(tmp_path, graph_store):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    manager = RepoWatcherManager(graph_store, debounce_seconds=0.2)
    manager.reindex("user-1", "repo-1", str(tmp_path))
    manager.watch("user-1", "repo-1", str(tmp_path))

    (tmp_path / "b.py").write_text("def bar():\n    pass\n")

    deadline = time.time() + 5
    found = False
    while time.time() < deadline:
        nodes, _ = graph_store.get_subgraph("user-1", "repo-1")
        if any(n["name"] == "bar" for n in nodes):
            found = True
            break
        time.sleep(0.1)

    manager.stop()
    assert found, "watcher did not pick up the new file within the timeout"


def test_resume_all_rewatches_repos_with_paths_that_still_exist(tmp_path, graph_store):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    graph_store.upsert_repo({
        "user_id": "user-1", "repo_id": "repo-1", "source": str(tmp_path),
        "local_path": str(tmp_path), "last_indexed_at": "2026-07-22T00:00:00",
    })
    graph_store.upsert_repo({
        "user_id": "user-1", "repo_id": "repo-gone", "source": "/does/not/exist",
        "local_path": "/does/not/exist", "last_indexed_at": "2026-07-22T00:00:00",
    })
    manager = RepoWatcherManager(graph_store, debounce_seconds=0.2)

    manager.resume_all()

    assert ("user-1", "repo-1") in manager.watched_repos()
    assert ("user-1", "repo-gone") not in manager.watched_repos()
    manager.stop()


def test_reindex_populates_code_symbol_embeddings(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())

    manager.reindex("user-1", "repo-1", str(tmp_path))

    count = qdrant.count(collection_name=CODE_SYMBOL_EMBEDDINGS).count
    assert count >= 1


def test_reindex_does_not_leak_stale_symbol_vectors(tmp_path, graph_store, qdrant):
    """Reindexing the same repo repeatedly must replace its vectors, not pile
    up a fresh duplicate copy on top of the last one every time (that's what
    was silently growing the collection unbounded on every debounced save)."""
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())

    manager.reindex("user-1", "repo-1", str(tmp_path))
    count_after_first = _count_for_repo(qdrant, "repo-1")
    manager.reindex("user-1", "repo-1", str(tmp_path))
    manager.reindex("user-1", "repo-1", str(tmp_path))

    count_after_third = _count_for_repo(qdrant, "repo-1")
    assert count_after_third == count_after_first, (
        f"vector count for repo-1 grew from {count_after_first} to {count_after_third} "
        "across repeated reindexes of an unchanged repo (stale duplicates)"
    )


def test_reindex_removes_vectors_for_deleted_symbols(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n\n\ndef bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))
    count_with_two_functions = _count_for_repo(qdrant, "repo-1")

    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    manager.reindex("user-1", "repo-1", str(tmp_path))

    count_with_one_function = _count_for_repo(qdrant, "repo-1")
    assert count_with_one_function < count_with_two_functions, (
        "vector for the removed `bar` symbol should be gone after reindex, "
        f"but count went from {count_with_two_functions} to {count_with_one_function}"
    )


def test_reindex_does_not_touch_another_repos_vectors(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-other", str(tmp_path))
    count_for_other_repo = _count_for_repo(qdrant, "repo-other")

    manager.reindex("user-1", "repo-1", str(tmp_path))
    manager.reindex("user-1", "repo-1", str(tmp_path))

    assert _count_for_repo(qdrant, "repo-other") == count_for_other_repo, (
        "repo-1's reindex must not delete or duplicate repo-other's vectors"
    )


def test_reindex_uses_qdrant_client_resolver_when_provided(tmp_path, graph_store, qdrant):
    """DEPLOY_MODE=local wires a resolver that opens a per-repo embedded Qdrant client
    (keyed by local_path) instead of one shared client -- reindex must call the resolver
    and write through the client it returns, ignoring the fixed qdrant_client fallback."""
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    resolver_calls = []

    def resolver(local_path):
        resolver_calls.append(local_path)
        return qdrant

    manager = RepoWatcherManager(
        graph_store, qdrant_client=object(), embedder=_FakeEmbedder(),
        qdrant_client_resolver=resolver,
    )

    manager.reindex("user-1", "repo-1", str(tmp_path))

    assert resolver_calls == [str(tmp_path)]
    assert qdrant.count(collection_name=CODE_SYMBOL_EMBEDDINGS).count >= 1


def test_concurrent_reindex_calls_for_same_repo_do_not_race(tmp_path, graph_store, qdrant):
    """Two reindex() calls for the same repo landing close together (e.g. two
    debounced fires where the first is still embedding) must serialize, not
    interleave their Qdrant delete+upsert -- interleaving is what produced
    'cannot commit - no transaction is active' against the real embedded
    Qdrant store and left duplicate/stale vectors behind."""
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_SlowFakeEmbedder(delay=0.3))

    manager.reindex("user-1", "repo-1", str(tmp_path))
    expected_count = _count_for_repo(qdrant, "repo-1")

    errors = []

    def run():
        try:
            manager.reindex("user-1", "repo-1", str(tmp_path))
        except Exception as exc:  # noqa: BLE001 - captured to fail the test with a message
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"concurrent reindex raised: {errors}"
    count = _count_for_repo(qdrant, "repo-1")
    assert count == expected_count, (
        f"expected {expected_count} vectors after serialized concurrent reindexes, found {count}"
    )


def test_reindex_paths_only_reembeds_the_changed_file(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))  # full baseline ingest
    nodes_before, _ = graph_store.get_subgraph("user-1", "repo-1")
    bar_before = next(n for n in nodes_before if n["name"] == "bar")

    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")  # only a.py changes

    embed_calls = []
    real_embed_batch = manager._embedder.embed_batch
    manager._embedder.embed_batch = lambda texts: (embed_calls.append(texts) or real_embed_batch(texts))

    manager.reindex_paths("user-1", "repo-1", str(tmp_path), {str(tmp_path / "a.py")}, set())

    nodes_after, _ = graph_store.get_subgraph("user-1", "repo-1")
    bar_after = next(n for n in nodes_after if n["name"] == "bar")
    assert bar_after == bar_before  # untouched file's symbol: same id, same content_hash
    # exactly one embed_batch call, covering only a.py's symbols (module + foo), not b.py's
    assert len(embed_calls) == 1
    assert not any("bar" in t for t in embed_calls[0])


def test_reindex_paths_removes_symbols_and_vectors_for_a_deleted_file(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))
    (tmp_path / "b.py").unlink()

    manager.reindex_paths("user-1", "repo-1", str(tmp_path), set(), {str(tmp_path / "b.py")})

    nodes, _ = graph_store.get_subgraph("user-1", "repo-1")
    assert "bar" not in {n["name"] for n in nodes}
    assert "foo" in {n["name"] for n in nodes}  # a.py untouched


def test_reindex_paths_resolves_a_call_from_a_changed_file_into_an_unchanged_files_symbol(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))

    (tmp_path / "a.py").write_text("def foo():\n    bar()\n")  # a.py now calls bar() from unchanged b.py

    manager.reindex_paths("user-1", "repo-1", str(tmp_path), {str(tmp_path / "a.py")}, set())

    nodes, edges = graph_store.get_subgraph("user-1", "repo-1")
    by_name = {n["name"]: n["id"] for n in nodes}
    calls = [e for e in edges if e["type"] == "CALLS"]
    assert any(e["source"] == by_name["foo"] and e["target"] == by_name["bar"] for e in calls)


def test_reindex_paths_drops_rather_than_reresolves_an_edge_from_an_unchanged_file_after_a_rename(
    tmp_path, graph_store, qdrant,
):
    """Documents the accepted edge-staleness policy (design spec section 'Edge staleness'):
    when file A renames a symbol that file B (unchanged) calls, the old CALLS edge is
    cleanly removed -- its target id no longer exists, and replace_files_in_repo's
    cascade delete (any edge touching a deleted symbol id) takes it out along with
    foo's old row, so it never dangles pointing at a nonexistent id. But since B isn't
    reparsed this pass, no *new* edge to foo_renamed is created either -- the CALLS
    relationship simply disappears from the graph until B itself is reindexed. This is
    intentional (not a bug): safe (no broken reference) but temporarily incomplete."""
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def use_it():\n    foo()\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))
    nodes, edges = graph_store.get_subgraph("user-1", "repo-1")
    old_foo_id = next(n["id"] for n in nodes if n["name"] == "foo")
    assert any(e["target"] == old_foo_id and e["type"] == "CALLS" for e in edges)

    (tmp_path / "a.py").write_text("def foo_renamed():\n    pass\n")  # rename, b.py untouched
    manager.reindex_paths("user-1", "repo-1", str(tmp_path), {str(tmp_path / "a.py")}, set())

    nodes, edges = graph_store.get_subgraph("user-1", "repo-1")
    calls = [e for e in edges if e["type"] == "CALLS"]
    assert calls == []  # old edge cascade-deleted with foo's old row, no new edge in its place
    assert "foo_renamed" in {n["name"] for n in nodes}  # the renamed symbol itself exists fine
    assert "use_it" in {n["name"] for n in nodes}  # b.py's own symbol untouched


def test_change_handler_buckets_create_modify_delete_and_move_events(tmp_path):
    from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent

    from app.graph.repo_watcher import _PendingChanges, _RepoChangeHandler

    pending = _PendingChanges()
    fired = []
    handler = _RepoChangeHandler(pending, lambda: fired.append(1), debounce_seconds=0.01)

    a = str(tmp_path / "a.py")
    b = str(tmp_path / "b.py")
    c = str(tmp_path / "c.py")

    handler.on_created(FileCreatedEvent(a))
    handler.on_modified(FileModifiedEvent(a))  # same file twice -> one entry
    handler.on_deleted(FileDeletedEvent(b))
    handler.on_moved(FileMovedEvent(c, str(tmp_path / "c2.py")))

    assert pending.changed == {a, str(tmp_path / "c2.py")}
    assert pending.deleted == {b, c}
    handler.cancel()


def test_change_handler_modify_then_delete_same_file_ends_up_deleted_only(tmp_path):
    from watchdog.events import FileDeletedEvent, FileModifiedEvent

    from app.graph.repo_watcher import _PendingChanges, _RepoChangeHandler

    pending = _PendingChanges()
    handler = _RepoChangeHandler(pending, lambda: None, debounce_seconds=0.01)
    a = str(tmp_path / "a.py")

    handler.on_modified(FileModifiedEvent(a))
    handler.on_deleted(FileDeletedEvent(a))

    assert pending.changed == set()
    assert pending.deleted == {a}
    handler.cancel()


def test_watch_reindexes_only_the_saved_file_end_to_end(tmp_path, graph_store, qdrant):
    """End-to-end through the real watchdog Observer (like the existing
    test_watch_reindexes_automatically_on_file_change), proving the batching
    handler + reindex_paths wiring in watch() actually works together, not
    just each piece in isolation."""
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder(), debounce_seconds=0.2)
    manager.reindex("user-1", "repo-1", str(tmp_path))
    nodes_before, _ = graph_store.get_subgraph("user-1", "repo-1")
    bar_id_before = next(n["id"] for n in nodes_before if n["name"] == "bar")

    manager.watch("user-1", "repo-1", str(tmp_path))
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")

    deadline = time.time() + 5
    found = False
    while time.time() < deadline:
        nodes, _ = graph_store.get_subgraph("user-1", "repo-1")
        bar_now = next((n for n in nodes if n["name"] == "bar"), None)
        if bar_now is not None and bar_now["id"] == bar_id_before and bar_now.get("content_hash"):
            found = True
            break
        time.sleep(0.1)

    manager.stop()
    assert found, "watcher did not settle on the expected post-reindex state within the timeout"


def test_reindex_paths_does_not_load_the_full_subgraph(tmp_path, graph_store, qdrant):
    # get_subgraph pulls every symbol row, every edge, and joined text entities.
    # Diffing one changed file only needs the narrow symbol index.
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")

    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("u1", "r1", str(tmp_path))

    calls = {"get_subgraph": 0, "list_symbol_index": 0}
    real_get_subgraph = graph_store.get_subgraph
    real_list_symbol_index = graph_store.list_symbol_index

    def counting_get_subgraph(user_id, repo_id):
        calls["get_subgraph"] += 1
        return real_get_subgraph(user_id, repo_id)

    def counting_list_symbol_index(user_id, repo_id):
        calls["list_symbol_index"] += 1
        return real_list_symbol_index(user_id, repo_id)

    graph_store.get_subgraph = counting_get_subgraph
    graph_store.list_symbol_index = counting_list_symbol_index

    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    manager.reindex_paths("u1", "r1", str(tmp_path), {str(tmp_path / "a.py")}, set())

    assert calls["get_subgraph"] == 0
    assert calls["list_symbol_index"] == 1
