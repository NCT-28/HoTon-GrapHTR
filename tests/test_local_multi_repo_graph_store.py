import os
import sqlite3

import pytest

from app.graph.code_graph_store import LocalMultiRepoGraphStore


@pytest.fixture
def central_path(tmp_path):
    return str(tmp_path / "central" / "graph.sqlite")


@pytest.fixture
def store(central_path):
    return LocalMultiRepoGraphStore(central_path)


def _repo_dir(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return str(d)


def _symbol(repo_id, sym_id, name="foo"):
    return {"id": sym_id, "user_id": "u1", "repo_id": repo_id, "kind": "function", "name": name,
            "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"}


def test_ping_returns_true(store):
    assert store.ping() is True


def test_replace_repo_graph_stores_symbols_under_repo_local_path_not_central_db(tmp_path, store, central_path):
    repo_path = _repo_dir(tmp_path, "repo1")

    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": repo_path, "local_path": repo_path, "last_indexed_at": "t1"},
        [_symbol("r1", "a")],
        [],
    )

    per_repo_db = os.path.join(repo_path, "graphtr-out", "graph.sqlite")
    assert os.path.exists(per_repo_db)

    conn = sqlite3.connect(per_repo_db)
    assert conn.execute("SELECT name FROM code_symbols WHERE id = 'a'").fetchone() == ("foo",)
    conn.close()

    # central registry db must not hold the symbol row -- only per-repo dbs do
    central_conn = sqlite3.connect(central_path)
    assert central_conn.execute("SELECT COUNT(*) FROM code_symbols").fetchone() == (0,)
    central_conn.close()


def test_get_subgraph_scoped_by_user_and_repo_across_separate_repo_dirs(tmp_path, store):
    repo1 = _repo_dir(tmp_path, "repo1")
    repo2 = _repo_dir(tmp_path, "repo2")
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": repo1, "local_path": repo1, "last_indexed_at": "t1"},
        [_symbol("r1", "a", "foo")], [],
    )
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r2", "source": repo2, "local_path": repo2, "last_indexed_at": "t1"},
        [_symbol("r2", "b", "bar")], [],
    )

    nodes, _ = store.get_subgraph("u1", "r1")

    assert [n["name"] for n in nodes] == ["foo"]


def test_get_repo_and_list_repos_come_from_central_registry(tmp_path, store):
    repo1 = _repo_dir(tmp_path, "repo1")
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": repo1, "local_path": repo1, "last_indexed_at": "t1"},
        [_symbol("r1", "a")], [],
    )

    assert store.get_repo("u1", "r1")["local_path"] == repo1
    assert [r["repo_id"] for r in store.list_repos()] == ["r1"]


def test_get_repo_returns_none_when_missing(store):
    assert store.get_repo("u1", "does-not-exist") is None


def test_get_subgraph_returns_empty_when_repo_not_registered(store):
    assert store.get_subgraph("u1", "does-not-exist") == ([], [])


def test_delete_repo_removes_symbols_and_registry_entry(tmp_path, store):
    repo1 = _repo_dir(tmp_path, "repo1")
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": repo1, "local_path": repo1, "last_indexed_at": "t1"},
        [_symbol("r1", "a")], [],
    )

    store.delete_repo("u1", "r1")

    assert store.get_repo("u1", "r1") is None
    assert store.get_subgraph("u1", "r1") == ([], [])


def test_list_code_symbols_aggregates_across_multiple_repo_dirs(tmp_path, store):
    repo1 = _repo_dir(tmp_path, "repo1")
    repo2 = _repo_dir(tmp_path, "repo2")
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": repo1, "local_path": repo1, "last_indexed_at": "t1"},
        [_symbol("r1", "a", "foo")], [],
    )
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r2", "source": repo2, "local_path": repo2, "last_indexed_at": "t1"},
        [_symbol("r2", "b", "bar")], [],
    )

    symbols = store.list_code_symbols("u1")

    assert {s["name"] for s in symbols} == {"foo", "bar"}


def test_get_subgraph_includes_text_entities_that_mention_a_symbol_in_a_repo_dir(tmp_path, store):
    repo1 = _repo_dir(tmp_path, "repo1")
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": repo1, "local_path": repo1, "last_indexed_at": "t1"},
        [_symbol("r1", "s1", "Retriever")], [],
    )
    store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])
    store.upsert_mentions_edges([{"source": "e1", "target": "s1"}])

    nodes, edges = store.get_subgraph("u1", "r1")

    assert {n["id"] for n in nodes} == {"s1", "e1"}
    assert {"source": "e1", "target": "s1", "type": "MENTIONS"} in edges
