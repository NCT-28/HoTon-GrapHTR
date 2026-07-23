"""Graph storage interface for hoton-graphtr's unified code-structure + text-entity
graph, plus the real Neo4j-backed implementation. `FakeGraphStore` (an
in-memory test double implementing the same interface) lives in
tests/conftest.py so every consumer of GraphStore can be unit-tested without a
live Neo4j instance."""

from abc import ABC, abstractmethod
from functools import lru_cache

from neo4j import GraphDatabase

from app.config import get_settings

# Edge-type labels are interpolated into Cypher relationship patterns (the
# driver has no way to parameterize a relationship type). Whitelisting against
# these fixed, code-controlled sets before interpolating prevents any
# possibility of Cypher injection through this path.
_CODE_EDGE_TYPES = {"DEFINES", "CALLS", "IMPORTS", "INHERITS"}


class GraphStore(ABC):
    # --- Phase 1: code graph ---

    @abstractmethod
    def upsert_repo(self, repo: dict) -> None: ...

    @abstractmethod
    def upsert_symbols(self, symbols: list[dict]) -> None: ...

    @abstractmethod
    def upsert_code_edges(self, edges: list[dict]) -> None: ...

    @abstractmethod
    def delete_repo(self, user_id: str, repo_id: str) -> None: ...

    @abstractmethod
    def get_repo(self, user_id: str, repo_id: str) -> dict | None: ...

    @abstractmethod
    def list_repos(self) -> list[dict]: ...

    @abstractmethod
    def get_subgraph(self, user_id: str, repo_id: str) -> tuple[list[dict], list[dict]]: ...

    @abstractmethod
    def ping(self) -> bool: ...

    # --- Phase 2: text entities + cross-link ---

    @abstractmethod
    def upsert_text_entities(self, entities: list[dict]) -> None: ...

    @abstractmethod
    def upsert_related_edges(self, edges: list[dict]) -> None: ...

    @abstractmethod
    def upsert_mentions_edges(self, edges: list[dict]) -> None: ...

    @abstractmethod
    def list_text_entities(self, user_id: str) -> list[dict]: ...

    @abstractmethod
    def list_code_symbols(self, user_id: str) -> list[dict]: ...


class Neo4jGraphStore(GraphStore):
    def __init__(self, driver):
        self._driver = driver

    def upsert_repo(self, repo: dict) -> None:
        self._driver.execute_query(
            """
            MERGE (r:Repo {repo_id: $repo_id, user_id: $user_id})
            SET r.source = $source, r.local_path = $local_path, r.last_indexed_at = $last_indexed_at
            """,
            **repo,
        )

    def upsert_symbols(self, symbols: list[dict]) -> None:
        if not symbols:
            return
        self._driver.execute_query(
            """
            UNWIND $symbols AS s
            MERGE (n:CodeSymbol {id: s.id})
            SET n.repo_id = s.repo_id, n.user_id = s.user_id, n.kind = s.kind,
                n.name = s.name, n.file_path = s.file_path, n.start_line = s.start_line,
                n.end_line = s.end_line, n.language = s.language
            """,
            symbols=symbols,
        )

    def upsert_code_edges(self, edges: list[dict]) -> None:
        by_type: dict[str, list[dict]] = {}
        for e in edges:
            if e["type"] not in _CODE_EDGE_TYPES:
                raise ValueError(f"unknown code edge type: {e['type']}")
            by_type.setdefault(e["type"], []).append({"source": e["source"], "target": e["target"]})

        for edge_type, batch in by_type.items():
            self._driver.execute_query(
                f"""
                UNWIND $batch AS e
                MATCH (a:CodeSymbol {{id: e.source}}), (b:CodeSymbol {{id: e.target}})
                MERGE (a)-[:{edge_type}]->(b)
                """,
                batch=batch,
            )

    def delete_repo(self, user_id: str, repo_id: str) -> None:
        self._driver.execute_query(
            "MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id}) DETACH DELETE n",
            user_id=user_id, repo_id=repo_id,
        )
        self._driver.execute_query(
            "MATCH (r:Repo {user_id: $user_id, repo_id: $repo_id}) DETACH DELETE r",
            user_id=user_id, repo_id=repo_id,
        )

    def get_repo(self, user_id: str, repo_id: str) -> dict | None:
        records, _, _ = self._driver.execute_query(
            "MATCH (r:Repo {user_id: $user_id, repo_id: $repo_id}) RETURN r",
            user_id=user_id, repo_id=repo_id,
        )
        return dict(records[0]["r"]) if records else None

    def list_repos(self) -> list[dict]:
        records, _, _ = self._driver.execute_query("MATCH (r:Repo) RETURN r")
        return [dict(record["r"]) for record in records]

    def get_subgraph(self, user_id: str, repo_id: str) -> tuple[list[dict], list[dict]]:
        """CodeSymbol nodes/edges for this repo, plus any TextEntity that
        MENTIONS one of those symbols — the unified-graph query surface, not
        just the code-only slice."""
        records, _, _ = self._driver.execute_query(
            """
            MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
            OPTIONAL MATCH (n)-[r]->(m:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
            OPTIONAL MATCH (te:TextEntity {user_id: $user_id})-[:MENTIONS]->(n)
            RETURN n, r, m, te
            """,
            user_id=user_id, repo_id=repo_id,
        )
        nodes_by_id: dict[str, dict] = {}
        edges: list[dict] = []
        for record in records:
            n = dict(record["n"])
            nodes_by_id[n["id"]] = n
            if record["r"] is not None:
                m = dict(record["m"])
                nodes_by_id[m["id"]] = m
                edges.append({"source": n["id"], "target": m["id"], "type": record["r"].type})
            if record["te"] is not None:
                te = dict(record["te"])
                nodes_by_id[te["id"]] = te
                edges.append({"source": te["id"], "target": n["id"], "type": "MENTIONS"})
        return list(nodes_by_id.values()), edges

    def ping(self) -> bool:
        self._driver.execute_query("RETURN 1")
        return True

    def upsert_text_entities(self, entities: list[dict]) -> None:
        if not entities:
            return
        self._driver.execute_query(
            """
            UNWIND $entities AS e
            MERGE (n:TextEntity {id: e.id})
            SET n.user_id = e.user_id, n.name = e.name, n.entity_type = e.entity_type,
                n.source_doc_id = e.source_doc_id, n.source_memory_id = e.source_memory_id
            """,
            entities=entities,
        )

    def upsert_related_edges(self, edges: list[dict]) -> None:
        if not edges:
            return
        self._driver.execute_query(
            """
            UNWIND $edges AS e
            MATCH (a:TextEntity {id: e.source}), (b:TextEntity {id: e.target})
            MERGE (a)-[:RELATED_TO]->(b)
            """,
            edges=edges,
        )

    def upsert_mentions_edges(self, edges: list[dict]) -> None:
        if not edges:
            return
        self._driver.execute_query(
            """
            UNWIND $edges AS e
            MATCH (a:TextEntity {id: e.source}), (b:CodeSymbol {id: e.target})
            MERGE (a)-[:MENTIONS]->(b)
            """,
            edges=edges,
        )

    def list_text_entities(self, user_id: str) -> list[dict]:
        records, _, _ = self._driver.execute_query(
            "MATCH (e:TextEntity {user_id: $user_id}) RETURN e", user_id=user_id
        )
        return [dict(r["e"]) for r in records]

    def list_code_symbols(self, user_id: str) -> list[dict]:
        records, _, _ = self._driver.execute_query(
            "MATCH (n:CodeSymbol {user_id: $user_id}) RETURN n", user_id=user_id
        )
        return [dict(r["n"]) for r in records]


@lru_cache
def get_graph_store() -> GraphStore:
    settings = get_settings()
    driver = GraphDatabase.driver(settings.neo4j_url, auth=(settings.neo4j_user, settings.neo4j_password))
    return Neo4jGraphStore(driver)
