from dataclasses import dataclass, field

import pytest
from qdrant_client import QdrantClient

from app.graph.code_graph_store import GraphStore
from app.clients.qdrant_store import bootstrap_collections
from app.dashboard.usage_store import UsageStore


@pytest.fixture
def qdrant() -> QdrantClient:
    client = QdrantClient(":memory:")
    bootstrap_collections(client, embed_dim=384)
    return client


@dataclass
class FakeGraphStore(GraphStore):
    text_entities: dict = field(default_factory=dict)
    related_edges: list = field(default_factory=list)
    mentions_edges: list = field(default_factory=list)

    def ping(self) -> bool:
        return True

    def upsert_text_entities(self, entities: list[dict]) -> None:
        for e in entities:
            self.text_entities[e["id"]] = e

    def upsert_related_edges(self, edges: list[dict]) -> None:
        self.related_edges.extend(edges)

    def upsert_mentions_edges(self, edges: list[dict]) -> None:
        self.mentions_edges.extend(edges)

    def list_text_entities(self, user_id: str) -> list[dict]:
        return [e for e in self.text_entities.values() if e["user_id"] == user_id]

    def list_code_symbols(self, user_id: str) -> list[dict]:
        # Code symbols are never stored anymore -- ingest_codebase writes graphtr-out/
        # instead. Kept so entity_linker's call site still resolves.
        return []

    def delete_text_entities_by_source_doc(self, user_id: str, source_doc_id: str) -> None:
        remove_ids = {
            eid for eid, e in self.text_entities.items()
            if e["user_id"] == user_id and e.get("source_doc_id") == source_doc_id
        }
        for eid in remove_ids:
            del self.text_entities[eid]
        self.related_edges = [
            e for e in self.related_edges if e["source"] not in remove_ids and e["target"] not in remove_ids
        ]
        self.mentions_edges = [e for e in self.mentions_edges if e["source"] not in remove_ids]


@pytest.fixture
def graph_store() -> FakeGraphStore:
    return FakeGraphStore()


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
