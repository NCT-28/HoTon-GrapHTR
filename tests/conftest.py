from dataclasses import dataclass, field

import pytest
from qdrant_client import QdrantClient

from app.clients.qdrant_store import bootstrap_collections
from app.dashboard.usage_store import UsageStore


@pytest.fixture
def qdrant() -> QdrantClient:
    client = QdrantClient(":memory:")
    bootstrap_collections(client, embed_dim=384)
    return client


@dataclass
class FakeUsageStore(UsageStore):
    events: list = field(default_factory=list)

    def record(self, event: dict) -> None:
        self.events.append(event)

    def counts_by_tool(self, since) -> list[dict]:
        by_tool: dict[str, dict] = {}
        for e in self.events:
            if e["created_at"] < since:
                continue
            row = by_tool.setdefault(e["tool_name"], {"tool_name": e["tool_name"], "calls": 0, "errors": 0, "durations": []})
            row["calls"] += 1
            if not e["success"]:
                row["errors"] += 1
            row["durations"].append(e["duration_ms"])
        result = []
        for row in by_tool.values():
            durations = sorted(row.pop("durations"))
            row["p50_ms"] = durations[len(durations) // 2] if durations else 0.0
            result.append(row)
        return result

    def counts_by_user(self, since) -> list[dict]:
        by_user: dict[str, int] = {}
        for e in self.events:
            if e["created_at"] < since:
                continue
            by_user[e["user_id"]] = by_user.get(e["user_id"], 0) + 1
        return [{"user_id": uid, "calls": calls} for uid, calls in by_user.items()]

    def ping(self) -> bool:
        return True


@pytest.fixture
def usage_store() -> FakeUsageStore:
    return FakeUsageStore()
