from dataclasses import dataclass, field

import pytest
from qdrant_client import QdrantClient

from app.graph.code_graph_store import GraphStore
from app.clients.qdrant_store import bootstrap_collections


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
