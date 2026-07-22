"""Pure graph-traversal algorithms over plain node/edge lists — no database
dependency, so these are testable in isolation. Mirrors graphify's
query/path/explain modes, applied to hoton-rag's own graph data."""

import networkx as nx


def _build_graph(nodes: list[dict], edges: list[dict]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for n in nodes:
        g.add_node(n["id"], **n)
    for e in edges:
        g.add_edge(e["source"], e["target"], type=e["type"])
    return g


def _find_by_name(g: nx.MultiDiGraph, name: str) -> str | None:
    for node_id, data in g.nodes(data=True):
        if data.get("name") == name:
            return node_id
    return None


def _edges_within(g: nx.MultiDiGraph, node_ids: set[str]) -> list[dict]:
    return [
        {"source": u, "target": v, "type": data["type"]}
        for u, v, data in g.edges(data=True)
        if u in node_ids and v in node_ids
    ]


def bfs_query(nodes: list[dict], edges: list[dict], keyword: str, depth: int = 2) -> tuple[list[dict], list[dict]]:
    """Seed on nodes whose name contains `keyword` (case-insensitive), then
    expand outward (both directions) up to `depth` hops. Returns the matched
    subgraph as (nodes, edges); ([], []) if nothing matches."""
    g = _build_graph(nodes, edges)
    keyword_lower = keyword.lower()
    seeds = {n for n, data in g.nodes(data=True) if keyword_lower in data.get("name", "").lower()}
    if not seeds:
        return [], []

    visited = set(seeds)
    frontier = set(seeds)
    for _ in range(depth):
        next_frontier: set[str] = set()
        for node_id in frontier:
            next_frontier.update(g.successors(node_id))
            next_frontier.update(g.predecessors(node_id))
        next_frontier -= visited
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier

    return [g.nodes[n] for n in visited], _edges_within(g, visited)


def shortest_path(
    nodes: list[dict], edges: list[dict], from_name: str, target_name: str
) -> tuple[list[dict], list[dict]] | None:
    """Shortest path (undirected — a CALLS or IMPORTS edge in either direction
    still counts as connected) between two nodes identified by name."""
    g = _build_graph(nodes, edges)
    from_id = _find_by_name(g, from_name)
    to_id = _find_by_name(g, target_name)
    if from_id is None or to_id is None:
        return None

    try:
        path_ids = nx.shortest_path(g.to_undirected(), from_id, to_id)
    except nx.NetworkXNoPath:
        return None

    path_set = set(path_ids)
    return [g.nodes[n] for n in path_ids], _edges_within(g, path_set)


def explain_node(nodes: list[dict], edges: list[dict], name: str) -> tuple[dict, list[dict], list[dict]] | None:
    """The node itself, its direct neighbors (both directions), and the edges
    connecting them. None if `name` isn't found."""
    g = _build_graph(nodes, edges)
    node_id = _find_by_name(g, name)
    if node_id is None:
        return None

    neighbor_ids = set(g.successors(node_id)) | set(g.predecessors(node_id))
    related_edges = [
        {"source": u, "target": v, "type": data["type"]}
        for u, v, data in g.edges(data=True)
        if u == node_id or v == node_id
    ]
    return g.nodes[node_id], [g.nodes[n] for n in neighbor_ids], related_edges
