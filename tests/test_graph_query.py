from app.graph_query import bfs_query, explain_node, shortest_path

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
