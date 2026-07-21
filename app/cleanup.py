"""Memory TTL pruning — ported from hoton-lmr/src/rag/cleanup.rs."""

import asyncio
import datetime
import logging

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointIdsList, Range

from app.qdrant_store import USER_MEMORIES

logger = logging.getLogger(__name__)


def run_memory_cleanup(client: QdrantClient) -> int:
    """Policy: deprecated memories unused 30+ days -> delete.
    Active memories with confidence < 0.2 unused 14+ days -> delete.
    Returns total rows deleted."""
    now = datetime.datetime.utcnow()
    deleted = 0

    deprecated_points, _ = client.scroll(
        collection_name=USER_MEMORIES,
        scroll_filter=Filter(must=[FieldCondition(key="status", match=MatchValue(value="deprecated"))]),
        limit=10000,
        with_payload=True,
    )
    stale_deprecated_ids = [
        p.id
        for p in deprecated_points
        if (now - datetime.datetime.fromisoformat(p.payload["last_used_at"])).days >= 30
    ]
    if stale_deprecated_ids:
        client.delete(collection_name=USER_MEMORIES, points_selector=PointIdsList(points=stale_deprecated_ids))
        deleted += len(stale_deprecated_ids)

    low_confidence_points, _ = client.scroll(
        collection_name=USER_MEMORIES,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="status", match=MatchValue(value="active")),
                FieldCondition(key="confidence", range=Range(lt=0.2)),
            ]
        ),
        limit=10000,
        with_payload=True,
    )
    stale_low_confidence_ids = [
        p.id
        for p in low_confidence_points
        if (now - datetime.datetime.fromisoformat(p.payload["last_used_at"])).days >= 14
    ]
    if stale_low_confidence_ids:
        client.delete(collection_name=USER_MEMORIES, points_selector=PointIdsList(points=stale_low_confidence_ids))
        deleted += len(stale_low_confidence_ids)

    return deleted


async def start_memory_cleanup_job(client: QdrantClient) -> None:
    """Runs memory cleanup every 24 hours. Intended to be launched as an asyncio task at startup."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            deleted = await asyncio.to_thread(run_memory_cleanup, client)
            if deleted:
                logger.info("rag cleanup: removed %d expired memory entries", deleted)
        except Exception:
            logger.warning("rag cleanup: cleanup job failed", exc_info=True)
