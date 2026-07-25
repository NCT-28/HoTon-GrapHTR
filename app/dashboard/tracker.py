"""One context manager wraps every MCP tool call and HTTP route hit, recording
metadata (never query/message content) to the UsageStore for /dashboard."""

import contextlib
import logging
import time
from datetime import datetime, timezone

from app.dashboard.usage_store import UsageStore

logger = logging.getLogger(__name__)

# tool_name values recorded from app/mcp_server.py's track_usage(...) calls --
# the actual MCP tool surface. Everything else recorded via track_usage is a
# REST route hit (app/rag/documents.py, memory.py, profile.py). Kept here,
# next to track_usage, so both call sites and the dashboard split stay in sync.
MCP_TOOL_NAMES = frozenset({
    "get_rag_context",
    "retrieve_chunks",
    "extract_and_store_memories",
    "update_profile_from_message",
    "embed_text",
    "ingest_codebase",
    "query_code_graph",
    "export_graph_snapshot",
})


@contextlib.contextmanager
def track_usage(store: "UsageStore | None", tool_name: str, user_id: str, repo_id: str | None = None):
    if store is None:
        yield
        return

    start = time.monotonic()
    error_message: str | None = None
    try:
        yield
    except Exception as e:
        error_message = str(e)[:500]
        raise
    finally:
        duration_ms = (time.monotonic() - start) * 1000
        # Usage tracking is observability, not part of the tool contract: a
        # broken UsageStore (e.g. a dead DB connection) must never turn a
        # successful tool call into a failure for the caller.
        try:
            store.record({
                "tool_name": tool_name,
                "user_id": user_id,
                "repo_id": repo_id,
                "success": error_message is None,
                "error_message": error_message,
                "duration_ms": duration_ms,
                "created_at": datetime.now(timezone.utc),
            })
        except Exception:
            logger.warning("usage_store.record failed for tool=%s", tool_name, exc_info=True)
