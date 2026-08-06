"""Read-side aggregation for GET /api/dashboard/summary: Qdrant storage sizes
and usage_events breakdowns by tool/user."""

from datetime import datetime, timedelta, timezone

from app.clients.qdrant_store import (
    PROFILE_SNAPSHOTS, RAG_CHUNKS, RAG_DOCUMENTS, USER_MEMORIES, USER_PROFILES,
)
from app.dashboard.tracker import MCP_TOOL_NAMES

_COLLECTIONS = [RAG_DOCUMENTS, RAG_CHUNKS, USER_MEMORIES, USER_PROFILES, PROFILE_SNAPSHOTS]


def storage_breakdown(client) -> list[dict]:
    result = []
    for name in _COLLECTIONS:
        try:
            points = client.count(collection_name=name).count
        except Exception:
            points = None
        result.append({"collection": name, "points": points})

    total = sum(r["points"] or 0 for r in result)
    for r in result:
        r["percent"] = round((r["points"] or 0) / total * 100, 1) if total else 0.0
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
    # Follows the scroll cursor to the end. The previous single limit=10000 call
    # dropped the returned offset, so a collection larger than one page produced
    # silently wrong counts rather than slow ones.
    counts: dict[str, int] = {}
    offset = None
    try:
        while True:
            points, offset = client.scroll(
                collection_name=collection, limit=1000, with_payload=["user_id"], offset=offset
            )
            for p in points:
                uid = p.payload.get("user_id")
                if uid:
                    counts[uid] = counts.get(uid, 0) + 1
            if offset is None:
                return counts
    except Exception:
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
