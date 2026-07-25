"""Read-side aggregation for GET /api/dashboard/summary: Qdrant storage sizes,
Neo4j project breakdown, and usage_events breakdowns by tool/user."""

from datetime import datetime, timedelta, timezone

from app.clients.qdrant_store import (
    CODE_SYMBOL_EMBEDDINGS, PROFILE_SNAPSHOTS, RAG_CHUNKS, RAG_DOCUMENTS, USER_MEMORIES, USER_PROFILES,
    count_code_symbol_embeddings,
)
from app.config import get_settings
from app.dashboard.tracker import MCP_TOOL_NAMES

_COLLECTIONS = [RAG_DOCUMENTS, RAG_CHUNKS, USER_MEMORIES, USER_PROFILES, PROFILE_SNAPSHOTS, CODE_SYMBOL_EMBEDDINGS]


def _code_symbol_embeddings_count_across_repos(graph_store) -> int:
    total = 0
    for repo in graph_store.list_repos():
        count = count_code_symbol_embeddings(None, graph_store, repo["user_id"], repo["repo_id"])
        total += count or 0
    return total


def storage_breakdown(client, graph_store=None) -> list[dict]:
    # DEPLOY_MODE=local writes code-symbol vectors into a per-repo embedded Qdrant
    # (see get_repo_qdrant_client) instead of the shared client, so that one collection
    # has to be summed across repos rather than counted on `client` like the others.
    fan_out_code_symbols = graph_store is not None and get_settings().deploy_mode == "local"

    result = []
    for name in _COLLECTIONS:
        if name == CODE_SYMBOL_EMBEDDINGS and fan_out_code_symbols:
            points = _code_symbol_embeddings_count_across_repos(graph_store)
        else:
            try:
                points = client.count(collection_name=name).count
            except Exception:
                points = None
        result.append({"collection": name, "points": points})

    total = sum(r["points"] or 0 for r in result)
    for r in result:
        r["percent"] = round((r["points"] or 0) / total * 100, 1) if total else 0.0
    return result


def project_breakdown(graph_store) -> list[dict]:
    if graph_store is None:
        return []
    result = []
    for repo in graph_store.list_repos():
        nodes, edges = graph_store.get_subgraph(repo["user_id"], repo["repo_id"])
        result.append({
            "repo_id": repo["repo_id"],
            "node_count": len(nodes),
            "edge_count": len(edges),
            "last_indexed_at": repo.get("last_indexed_at"),
        })
    return result


def mcp_tool_usage(usage_store, hours: int = 24) -> list[dict]:
    if usage_store is None:
        return []
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [r for r in usage_store.counts_by_tool(since) if r["tool_name"] in MCP_TOOL_NAMES]


def route_usage(usage_store, hours: int = 24) -> list[dict]:
    if usage_store is None:
        return []
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [r for r in usage_store.counts_by_tool(since) if r["tool_name"] not in MCP_TOOL_NAMES]


def _count_by_user_id(client, collection: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        points, _ = client.scroll(collection_name=collection, limit=10000, with_payload=["user_id"])
    except Exception:
        return counts
    for p in points:
        uid = p.payload.get("user_id")
        if uid:
            counts[uid] = counts.get(uid, 0) + 1
    return counts


def user_breakdown(client, usage_store, hours: int = 24) -> list[dict]:
    if usage_store is None:
        return []
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    tool_counts = {row["user_id"]: row["calls"] for row in usage_store.counts_by_user(since)}
    doc_counts = _count_by_user_id(client, RAG_DOCUMENTS)
    memory_counts = _count_by_user_id(client, USER_MEMORIES)
    return [
        {
            "user_id": uid,
            "tool_calls": calls,
            "doc_count": doc_counts.get(uid, 0),
            "memory_count": memory_counts.get(uid, 0),
        }
        for uid, calls in tool_counts.items()
    ]
