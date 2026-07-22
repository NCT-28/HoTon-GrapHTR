def test_fake_graph_store_ping_returns_true(graph_store):
    assert graph_store.ping() is True


def test_upsert_and_get_subgraph_scoped_by_user_and_repo(graph_store):
    graph_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "b", "user_id": "u1", "repo_id": "r2", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    graph_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    nodes, edges = graph_store.get_subgraph("u1", "r1")

    assert [n["name"] for n in nodes] == ["foo"]
    assert edges == []  # b is in a different repo, so the cross-repo edge is excluded


def test_delete_repo_removes_its_symbols_and_edges(graph_store):
    graph_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "c", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "baz",
         "file_path": "c.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    graph_store.upsert_code_edges([{"source": "a", "target": "c", "type": "CALLS"}])

    graph_store.delete_repo("u1", "r1")

    nodes, edges = graph_store.get_subgraph("u1", "r1")
    assert nodes == []
    assert edges == []


def test_get_subgraph_includes_text_entities_that_mention_a_symbol(graph_store):
    graph_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 10, "language": "python"},
    ])
    graph_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])
    graph_store.upsert_mentions_edges([{"source": "e1", "target": "s1"}])

    nodes, edges = graph_store.get_subgraph("u1", "r1")

    assert {n["id"] for n in nodes} == {"s1", "e1"}
    assert {"source": "e1", "target": "s1", "type": "MENTIONS"} in edges


def test_list_repos_returns_all_repos_across_users(graph_store):
    graph_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                              "local_path": "/tmp/r1", "last_indexed_at": "2026-07-22T00:00:00"})
    graph_store.upsert_repo({"user_id": "u2", "repo_id": "r2", "source": "/tmp/r2",
                              "local_path": "/tmp/r2", "last_indexed_at": "2026-07-22T00:00:00"})

    repos = graph_store.list_repos()

    assert {r["repo_id"] for r in repos} == {"r1", "r2"}
