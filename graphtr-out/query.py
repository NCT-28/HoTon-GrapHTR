#!/usr/bin/env python3
"""Local, offline query over rag-out/graph.json (no MCP call needed).

Modes:
  query "<keyword>"          keyword match + BFS neighborhood (depth 2)
  path "<from>" "<to>"       shortest path between two symbol names
  explain "<name>"           node + direct neighbors (depth 1)

Usage:
  python3 rag-out/query.py query worker
  python3 rag-out/query.py path list_files ingest_codebase
  python3 rag-out/query.py explain InferenceRequest
"""
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

GRAPH_PATH = Path(__file__).parent / "graph.json"


def load_graph():
    data = json.loads(GRAPH_PATH.read_text())
    nodes_by_id = {n["id"]: n for n in data["nodes"]}
    adj = defaultdict(list)  # id -> [(neighbor_id, edge_type, direction)]
    for e in data["edges"]:
        adj[e["source"]].append((e["target"], e["type"], "out"))
        adj[e["target"]].append((e["source"], e["type"], "in"))
    return nodes_by_id, adj


def find_by_name(nodes_by_id, name):
    name_l = name.lower()
    exact = [n for n in nodes_by_id.values() if n["name"] == name]
    if exact:
        return exact
    return [n for n in nodes_by_id.values() if name_l in n["name"].lower()]


def bfs_subgraph(nodes_by_id, adj, seed_ids, depth):
    seen = set(seed_ids)
    frontier = set(seed_ids)
    for _ in range(depth):
        nxt = set()
        for nid in frontier:
            for neighbor_id, _etype, _dir in adj.get(nid, []):
                if neighbor_id not in seen:
                    nxt.add(neighbor_id)
                    seen.add(neighbor_id)
        frontier = nxt
        if not frontier:
            break
    sub_nodes = [nodes_by_id[i] for i in seen if i in nodes_by_id]
    sub_edges = []
    for nid in seen:
        for neighbor_id, etype, direction in adj.get(nid, []):
            if direction == "out" and neighbor_id in seen:
                sub_edges.append({"source": nid, "target": neighbor_id, "type": etype})
    return sub_nodes, sub_edges


def shortest_path(adj, start_id, goal_id):
    if start_id == goal_id:
        return [start_id]
    visited = {start_id}
    queue = deque([(start_id, [start_id])])
    while queue:
        cur, path = queue.popleft()
        for neighbor_id, _etype, _dir in adj.get(cur, []):
            if neighbor_id == goal_id:
                return path + [neighbor_id]
            if neighbor_id not in visited:
                visited.add(neighbor_id)
                queue.append((neighbor_id, path + [neighbor_id]))
    return None


def cmd_query(keyword, depth=2):
    nodes_by_id, adj = load_graph()
    matches = find_by_name(nodes_by_id, keyword)
    if not matches:
        print(json.dumps({"nodes": [], "edges": [], "note": f"no symbol matching '{keyword}'"}))
        return
    seed_ids = [n["id"] for n in matches]
    sub_nodes, sub_edges = bfs_subgraph(nodes_by_id, adj, seed_ids, depth)
    print(json.dumps({"nodes": sub_nodes, "edges": sub_edges}, indent=2))


def cmd_path(from_name, to_name):
    nodes_by_id, adj = load_graph()
    starts = find_by_name(nodes_by_id, from_name)
    goals = find_by_name(nodes_by_id, to_name)
    if not starts or not goals:
        print(json.dumps({"path": None, "note": "from or to symbol not found"}))
        return
    best = None
    for s in starts:
        for g in goals:
            p = shortest_path(adj, s["id"], g["id"])
            if p and (best is None or len(p) < len(best)):
                best = p
    if not best:
        print(json.dumps({"path": None, "note": "no path found"}))
        return
    path_nodes = [nodes_by_id[i] for i in best]
    print(json.dumps({"path": path_nodes}, indent=2))


def cmd_explain(name):
    nodes_by_id, adj = load_graph()
    matches = find_by_name(nodes_by_id, name)
    if not matches:
        print(json.dumps({"node": None, "neighbors": [], "note": f"no symbol matching '{name}'"}))
        return
    node = matches[0]
    neighbors = []
    for neighbor_id, etype, direction in adj.get(node["id"], []):
        n = nodes_by_id.get(neighbor_id)
        if n:
            neighbors.append({**n, "relation": etype, "direction": direction})
    print(json.dumps({"node": node, "neighbors": neighbors}, indent=2))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "query" and len(sys.argv) >= 3:
        cmd_query(sys.argv[2])
    elif mode == "path" and len(sys.argv) >= 4:
        cmd_path(sys.argv[2], sys.argv[3])
    elif mode == "explain" and len(sys.argv) >= 3:
        cmd_explain(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
