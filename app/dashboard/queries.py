"""Read-side aggregation for GET /api/dashboard/summary: Qdrant storage sizes,
Neo4j project breakdown, and usage_events breakdowns by tool/user."""

from datetime import datetime, timedelta, timezone

from app.clients.qdrant_store import (
    CODE_SYMBOL_EMBEDDINGS, PROFILE_SNAPSHOTS, RAG_CHUNKS, RAG_DOCUMENTS, USER_MEMORIES, USER_PROFILES,
)

_COLLECTIONS = [RAG_DOCUMENTS, RAG_CHUNKS, USER_MEMORIES, USER_PROFILES, PROFILE_SNAPSHOTS, CODE_SYMBOL_EMBEDDINGS]


def storage_breakdown(client) -> list[dict]:
    result = []
    for name in _COLLECTIONS:
        try:
            points = client.count(collection_name=name).count
        except Exception:
            points = None
        result.append({"collection": name, "points": points})
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


def tool_usage(usage_store, hours: int = 24) -> list[dict]:
    if usage_store is None:
        return []
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return usage_store.counts_by_tool(since)


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
