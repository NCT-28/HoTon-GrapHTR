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
