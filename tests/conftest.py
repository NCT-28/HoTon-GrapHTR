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
    repos: dict = field(default_factory=dict)
    symbols: dict = field(default_factory=dict)
    code_edges: list = field(default_factory=list)
    text_entities: dict = field(default_factory=dict)
    related_edges: list = field(default_factory=list)
    mentions_edges: list = field(default_factory=list)

    def upsert_repo(self, repo: dict) -> None:
        self.repos[(repo["user_id"], repo["repo_id"])] = repo

    def upsert_symbols(self, symbols: list[dict]) -> None:
        for s in symbols:
            self.symbols[s["id"]] = s

    def upsert_code_edges(self, edges: list[dict]) -> None:
        self.code_edges.extend(edges)

    def delete_repo(self, user_id: str, repo_id: str) -> None:
        self.repos.pop((user_id, repo_id), None)
        keep_ids = set()
        for sid, s in list(self.symbols.items()):
            if s["user_id"] == user_id and s["repo_id"] == repo_id:
                del self.symbols[sid]
            else:
                keep_ids.add(sid)
        self.code_edges = [e for e in self.code_edges if e["source"] in keep_ids and e["target"] in keep_ids]

    def get_repo(self, user_id: str, repo_id: str) -> dict | None:
        return self.repos.get((user_id, repo_id))

    def list_repos(self) -> list[dict]:
        return list(self.repos.values())

    def get_subgraph(self, user_id: str, repo_id: str) -> tuple[list[dict], list[dict]]:
        nodes_by_id = {
            s["id"]: s for s in self.symbols.values() if s["user_id"] == user_id and s["repo_id"] == repo_id
        }
        ids = set(nodes_by_id.keys())
        edges = [e for e in self.code_edges if e["source"] in ids and e["target"] in ids]
        for e in self.mentions_edges:
            if e["target"] in ids:
                te = self.text_entities.get(e["source"])
                if te is not None:
                    nodes_by_id[te["id"]] = te
                edges.append({"source": e["source"], "target": e["target"], "type": "MENTIONS"})
        return list(nodes_by_id.values()), edges

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
        return [s for s in self.symbols.values() if s["user_id"] == user_id]


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
