import pytest

from app.graph.code_graph_store import SqliteGraphStore


@pytest.fixture
def sqlite_store(tmp_path):
    return SqliteGraphStore(str(tmp_path / "graph.sqlite"))


def test_sqlite_graph_store_ping_returns_true(sqlite_store):
    assert sqlite_store.ping() is True


def test_sqlite_upsert_and_get_subgraph_scoped_by_user_and_repo(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "b", "user_id": "u1", "repo_id": "r2", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    sqlite_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")

    assert [n["name"] for n in nodes] == ["foo"]
    assert edges == []  # b is in a different repo, so the cross-repo edge is excluded


def test_sqlite_upsert_symbols_is_idempotent(sqlite_store):
    symbol = {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
              "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"}
    sqlite_store.upsert_symbols([symbol])
    sqlite_store.upsert_symbols([{**symbol, "name": "foo_renamed"}])

    nodes, _ = sqlite_store.get_subgraph("u1", "r1")

    assert [n["name"] for n in nodes] == ["foo_renamed"]


def test_sqlite_delete_repo_removes_its_symbols_and_edges(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "c", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "baz",
         "file_path": "c.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    sqlite_store.upsert_code_edges([{"source": "a", "target": "c", "type": "CALLS"}])

    sqlite_store.delete_repo("u1", "r1")

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")
    assert nodes == []
    assert edges == []


def test_sqlite_get_subgraph_includes_text_entities_that_mention_a_symbol(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 10, "language": "python"},
    ])
    sqlite_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])
    sqlite_store.upsert_mentions_edges([{"source": "e1", "target": "s1"}])

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")

    assert {n["id"] for n in nodes} == {"s1", "e1"}
    assert {"source": "e1", "target": "s1", "type": "MENTIONS"} in edges


def test_sqlite_list_repos_returns_all_repos_across_users(sqlite_store):
    sqlite_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                               "local_path": "/tmp/r1", "last_indexed_at": "2026-07-22T00:00:00"})
    sqlite_store.upsert_repo({"user_id": "u2", "repo_id": "r2", "source": "/tmp/r2",
                               "local_path": "/tmp/r2", "last_indexed_at": "2026-07-22T00:00:00"})

    repos = sqlite_store.list_repos()

    assert {r["repo_id"] for r in repos} == {"r1", "r2"}


def test_sqlite_get_repo_returns_none_when_missing(sqlite_store):
    assert sqlite_store.get_repo("u1", "does-not-exist") is None


def test_sqlite_delete_text_entities_by_source_doc_removes_entity_and_edges(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 10, "language": "python"},
    ])
    sqlite_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])
    sqlite_store.upsert_mentions_edges([{"source": "e1", "target": "s1"}])

    sqlite_store.delete_text_entities_by_source_doc("u1", "doc-1")

    assert sqlite_store.list_text_entities("u1") == []
    nodes, edges = sqlite_store.get_subgraph("u1", "r1")
    assert edges == []


def test_sqlite_replace_repo_graph_rolls_back_entirely_on_invalid_edge_type(sqlite_store):
    sqlite_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                               "local_path": "/tmp/r1", "last_indexed_at": "2026-07-22T00:00:00"})
    sqlite_store.upsert_symbols([
        {"id": "old", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "old_fn",
         "file_path": "old.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])

    with pytest.raises(ValueError, match="unknown code edge type"):
        sqlite_store.replace_repo_graph(
            {"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
             "local_path": "/tmp/r1", "last_indexed_at": "2026-07-22T01:00:00"},
            [{"id": "new", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "new_fn",
              "file_path": "new.py", "start_line": 1, "end_line": 2, "language": "python"}],
            [{"source": "new", "target": "new", "type": "NOT_A_REAL_TYPE"}],
        )

    # the whole replace must have rolled back -- old data still intact, new data absent
    nodes, _ = sqlite_store.get_subgraph("u1", "r1")
    assert [n["name"] for n in nodes] == ["old_fn"]


def test_sqlite_replace_files_in_repo_only_touches_symbols_in_stale_file_paths(sqlite_store):
    sqlite_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                               "local_path": "/tmp/r1", "last_indexed_at": "t0"})
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "hash-a"},
        {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "hash-b"},
    ])
    sqlite_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    sqlite_store.replace_files_in_repo(
        {"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1", "local_path": "/tmp/r1", "last_indexed_at": "t1"},
        ["a.py"],
        [{"id": "a2", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo_renamed",
          "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
          "content_hash": "hash-a2"}],
        [],
    )

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")
    by_name = {n["name"]: n for n in nodes}
    assert "foo" not in by_name  # old a.py symbol gone
    assert by_name["foo_renamed"]["content_hash"] == "hash-a2"
    assert by_name["bar"]["content_hash"] == "hash-b"  # untouched b.py symbol keeps its content_hash
    assert edges == []  # the CALLS edge referencing the deleted "a" id is gone too
    assert sqlite_store.get_repo("u1", "r1")["last_indexed_at"] == "t1"


def test_sqlite_replace_files_in_repo_with_no_symbols_just_deletes_stale_files(sqlite_store):
    sqlite_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                               "local_path": "/tmp/r1", "last_indexed_at": "t0"})
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "h"},
    ])

    sqlite_store.replace_files_in_repo(
        {"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1", "local_path": "/tmp/r1", "last_indexed_at": "t1"},
        ["a.py"], [], [],
    )

    nodes, _ = sqlite_store.get_subgraph("u1", "r1")
    assert nodes == []


def test_sqlite_content_hash_column_migrates_on_pre_existing_db(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "old.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE code_symbols (id TEXT PRIMARY KEY, repo_id TEXT, user_id TEXT, kind TEXT, "
        "name TEXT, file_path TEXT, start_line INTEGER, end_line INTEGER, language TEXT)"
    )
    conn.commit()
    conn.close()

    store = SqliteGraphStore(db_path)  # must not raise

    store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "h"},
    ])
    nodes, _ = store.get_subgraph("u1", "r1")
    assert nodes[0]["content_hash"] == "h"


def test_get_graph_store_returns_local_multi_repo_store_in_local_deploy_mode(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.graph.code_graph_store import LocalMultiRepoGraphStore, get_graph_store

    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_graph_store.cache_clear()

    store = get_graph_store()

    # local mode routes each repo's symbols/edges into its own directory (see
    # LocalMultiRepoGraphStore) instead of one shared file, so a large/many-repo
    # local install doesn't grow a single db under local_data_dir without bound.
    assert isinstance(store, LocalMultiRepoGraphStore)
    assert (tmp_path / "graph.sqlite").exists()

    get_graph_store.cache_clear()
    get_settings.cache_clear()
