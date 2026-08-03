from app.graph.graph_query import bfs_query, explain_node, fuse_graph_context, shortest_path

NODES = [
    {"id": "1", "name": "Animal", "kind": "class"},
    {"id": "2", "name": "Dog", "kind": "class"},
    {"id": "3", "name": "bark", "kind": "method"},
    {"id": "4", "name": "helper", "kind": "function"},
    {"id": "5", "name": "unrelated", "kind": "function"},
]
EDGES = [
    {"source": "2", "target": "1", "type": "INHERITS"},
    {"source": "2", "target": "3", "type": "DEFINES"},
    {"source": "3", "target": "4", "type": "CALLS"},
]


def test_bfs_query_finds_matching_node_and_neighbors_within_depth():
    nodes, edges = bfs_query(NODES, EDGES, keyword="dog", depth=1)
    names = {n["name"] for n in nodes}
    assert names == {"Dog", "Animal", "bark"}
    assert {"unrelated"}.isdisjoint(names)


def test_bfs_query_respects_depth():
    nodes, _ = bfs_query(NODES, EDGES, keyword="dog", depth=2)
    names = {n["name"] for n in nodes}
    assert "helper" in names  # 2 hops from Dog via bark -> helper


def test_bfs_query_returns_empty_when_no_match():
    nodes, edges = bfs_query(NODES, EDGES, keyword="nonexistent", depth=2)
    assert nodes == []
    assert edges == []


def test_shortest_path_between_two_named_nodes():
    nodes, edges = shortest_path(NODES, EDGES, from_name="Dog", target_name="helper")
    names = [n["name"] for n in nodes]
    assert names == ["Dog", "bark", "helper"]
    assert len(edges) == 2


def test_shortest_path_returns_none_when_no_path_exists():
    assert shortest_path(NODES, EDGES, from_name="Dog", target_name="unrelated") is None


def test_shortest_path_returns_none_for_unknown_name():
    assert shortest_path(NODES, EDGES, from_name="Dog", target_name="nope") is None


def test_explain_node_returns_node_and_direct_neighbors():
    center, neighbors, edges = explain_node(NODES, EDGES, name="Dog")
    assert center["name"] == "Dog"
    assert {n["name"] for n in neighbors} == {"Animal", "bark"}
    assert len(edges) == 2


def test_explain_node_returns_none_for_unknown_name():
    assert explain_node(NODES, EDGES, name="nope") is None


class _FakeGraphStore:
    def __init__(self, nodes, edges):
        self._nodes = nodes
        self._edges = edges

    def get_subgraph(self, user_id, repo_id):
        return self._nodes, self._edges


def test_fuse_graph_context_merges_multiple_keywords_dedup():
    store = _FakeGraphStore(NODES, EDGES)
    nodes, edges = fuse_graph_context(store, "u1", "r1", ["dog", "bark"], max_nodes=15)
    names = {n["name"] for n in nodes}
    assert names == {"Dog", "Animal", "bark", "helper"}


def test_fuse_graph_context_respects_max_nodes_cap():
    store = _FakeGraphStore(NODES, EDGES)
    nodes, _edges = fuse_graph_context(store, "u1", "r1", ["dog"], max_nodes=1)
    assert len(nodes) == 1


def test_fuse_graph_context_empty_keywords_returns_empty():
    store = _FakeGraphStore(NODES, EDGES)
    assert fuse_graph_context(store, "u1", "r1", []) == ([], [])


def test_fuse_graph_context_filters_edges_to_kept_nodes():
    store = _FakeGraphStore(NODES, EDGES)
    nodes, edges = fuse_graph_context(store, "u1", "r1", ["dog"], max_nodes=15)
    kept_ids = {n["id"] for n in nodes}
    assert all(e["source"] in kept_ids and e["target"] in kept_ids for e in edges)


def test_fuse_graph_context_builds_the_graph_once_across_keywords(monkeypatch, graph_store):
    from app.graph import graph_query

    graph_store.upsert_repo({
        "user_id": "u1", "repo_id": "r1", "source": "s",
        "local_path": "/tmp/r1", "last_indexed_at": "now",
    })
    graph_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "retrieve_chunks",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Embedder",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "c", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "rerank",
         "file_path": "c.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    graph_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    builds = {"count": 0}
    real_build_graph = graph_query._build_graph

    def counting_build_graph(nodes, edges):
        builds["count"] += 1
        return real_build_graph(nodes, edges)

    monkeypatch.setattr(graph_query, "_build_graph", counting_build_graph)

    nodes, _edges = graph_query.fuse_graph_context(
        graph_store, "u1", "r1", ["retrieve_chunks", "Embedder", "rerank"]
    )

    assert {n["name"] for n in nodes} == {"retrieve_chunks", "Embedder", "rerank"}
    assert builds["count"] == 1
