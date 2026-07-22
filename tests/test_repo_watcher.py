import time

from app.qdrant_store import CODE_SYMBOL_EMBEDDINGS
from app.repo_watcher import RepoWatcherManager


class _FakeEmbedder:
    def embed_batch(self, texts):
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
