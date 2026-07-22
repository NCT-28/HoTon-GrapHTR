"""One context manager wraps every MCP tool call and HTTP route hit, recording
metadata (never query/message content) to the UsageStore for /dashboard."""

import contextlib
import time
from datetime import datetime, timezone

from app.dashboard.usage_store import UsageStore


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
        store.record({
            "tool_name": tool_name,
            "user_id": user_id,
            "repo_id": repo_id,
            "success": error_message is None,
            "error_message": error_message,
            "duration_ms": duration_ms,
            "created_at": datetime.now(timezone.utc),
        })
