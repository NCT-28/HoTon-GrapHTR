"""Graph storage interface for hoton-graphtr's unified code-structure + text-entity
graph, plus the real Neo4j-backed implementation. `FakeGraphStore` (an
in-memory test double implementing the same interface) lives in
tests/conftest.py so every consumer of GraphStore can be unit-tested without a
live Neo4j instance."""

import os
import sqlite3
import threading
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
    def replace_repo_graph(self, repo: dict, symbols: list[dict], edges: list[dict]) -> None:
        """Atomically replace a repo's entire code graph (old symbols/edges deleted, new
        repo/symbols/edges written) as a single unit -- a reader must never observe a
        partial state (e.g. all-symbols-no-edges) mid-replace."""
        ...

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

    @abstractmethod
    def delete_text_entities_by_source_doc(self, user_id: str, source_doc_id: str) -> None: ...


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
        def _work(tx):
            tx.run(
                "MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id}) DETACH DELETE n",
                user_id=user_id, repo_id=repo_id,
            )
            tx.run(
                "MATCH (r:Repo {user_id: $user_id, repo_id: $repo_id}) DETACH DELETE r",
                user_id=user_id, repo_id=repo_id,
            )

        with self._driver.session() as session:
            session.execute_write(_work)

    def replace_repo_graph(self, repo: dict, symbols: list[dict], edges: list[dict]) -> None:
        user_id, repo_id = repo["user_id"], repo["repo_id"]

        def _work(tx):
            tx.run(
                "MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id}) DETACH DELETE n",
                user_id=user_id, repo_id=repo_id,
            )
            tx.run(
                "MATCH (r:Repo {user_id: $user_id, repo_id: $repo_id}) DETACH DELETE r",
                user_id=user_id, repo_id=repo_id,
            )
            tx.run(
                """
                MERGE (r:Repo {repo_id: $repo_id, user_id: $user_id})
                SET r.source = $source, r.local_path = $local_path, r.last_indexed_at = $last_indexed_at
                """,
                **repo,
            )
            if symbols:
                tx.run(
                    """
                    UNWIND $symbols AS s
                    MERGE (n:CodeSymbol {id: s.id})
                    SET n.repo_id = s.repo_id, n.user_id = s.user_id, n.kind = s.kind,
                        n.name = s.name, n.file_path = s.file_path, n.start_line = s.start_line,
                        n.end_line = s.end_line, n.language = s.language
                    """,
                    symbols=symbols,
                )

            by_type: dict[str, list[dict]] = {}
            for e in edges:
                if e["type"] not in _CODE_EDGE_TYPES:
                    raise ValueError(f"unknown code edge type: {e['type']}")
                by_type.setdefault(e["type"], []).append({"source": e["source"], "target": e["target"]})

            for edge_type, batch in by_type.items():
                tx.run(
                    f"""
                    UNWIND $batch AS e
                    MATCH (a:CodeSymbol {{id: e.source}}), (b:CodeSymbol {{id: e.target}})
                    MERGE (a)-[:{edge_type}]->(b)
                    """,
                    batch=batch,
                )

        with self._driver.session() as session:
            session.execute_write(_work)

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
        # Two separate queries, not one query with two chained OPTIONAL MATCHes:
        # independent optional matches in a single Cypher query cross-join per
        # row, so a symbol with both outgoing code edges and an incoming
        # MENTIONS would otherwise have each (r,m) pair duplicated once per
        # `te` match and vice versa.
        code_records, _, _ = self._driver.execute_query(
            """
            MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
            OPTIONAL MATCH (n)-[r]->(m:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
            RETURN n, r, m
            """,
            user_id=user_id, repo_id=repo_id,
        )
        mention_records, _, _ = self._driver.execute_query(
            """
            MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
            OPTIONAL MATCH (te:TextEntity {user_id: $user_id})-[:MENTIONS]->(n)
            RETURN n, te
            """,
            user_id=user_id, repo_id=repo_id,
        )

        nodes_by_id: dict[str, dict] = {}
        edges: list[dict] = []
        for record in code_records:
            n = dict(record["n"])
            nodes_by_id[n["id"]] = n
            if record["r"] is not None:
                m = dict(record["m"])
                nodes_by_id[m["id"]] = m
                edges.append({"source": n["id"], "target": m["id"], "type": record["r"].type})
        for record in mention_records:
            n = dict(record["n"])
            nodes_by_id[n["id"]] = n
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

    def delete_text_entities_by_source_doc(self, user_id: str, source_doc_id: str) -> None:
        self._driver.execute_query(
            "MATCH (e:TextEntity {user_id: $user_id, source_doc_id: $source_doc_id}) DETACH DELETE e",
            user_id=user_id, source_doc_id=source_doc_id,
        )


def _in_clause(count: int) -> str:
    return "(" + ",".join("?" * count) + ")"


class SqliteGraphStore(GraphStore):
    """File-backed GraphStore for DEPLOY_MODE=local. No Cypher-equivalent
    traversal is needed here: BFS/shortest-path/explain already run in Python
    via networkx (app/graph/graph_query.py) against whatever get_subgraph()
    returns, so this only has to do plain CRUD."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repos (
                    user_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    source TEXT,
                    local_path TEXT,
                    last_indexed_at TEXT,
                    PRIMARY KEY (user_id, repo_id)
                );
                CREATE TABLE IF NOT EXISTS code_symbols (
                    id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    kind TEXT,
                    name TEXT,
                    file_path TEXT,
                    start_line INTEGER,
                    end_line INTEGER,
                    language TEXT
                );
                CREATE INDEX IF NOT EXISTS code_symbols_scope_idx ON code_symbols (user_id, repo_id);
                CREATE TABLE IF NOT EXISTS code_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    type TEXT NOT NULL,
                    UNIQUE (source, target, type)
                );
                CREATE TABLE IF NOT EXISTS text_entities (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT,
                    entity_type TEXT,
                    source_doc_id TEXT,
                    source_memory_id TEXT
                );
                CREATE INDEX IF NOT EXISTS text_entities_user_idx ON text_entities (user_id);
                CREATE TABLE IF NOT EXISTS related_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    UNIQUE (source, target)
                );
                CREATE TABLE IF NOT EXISTS mentions_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    UNIQUE (source, target)
                );
                CREATE INDEX IF NOT EXISTS mentions_edges_target_idx ON mentions_edges (target);
                """
            )

    # --- unlocked helpers, only ever called from inside a `with self._lock, self._conn:` block ---

    def _upsert_repo_unlocked(self, repo: dict) -> None:
        self._conn.execute(
            """
            INSERT INTO repos (user_id, repo_id, source, local_path, last_indexed_at)
            VALUES (:user_id, :repo_id, :source, :local_path, :last_indexed_at)
            ON CONFLICT (user_id, repo_id) DO UPDATE SET
                source = excluded.source, local_path = excluded.local_path,
                last_indexed_at = excluded.last_indexed_at
            """,
            repo,
        )

    def _upsert_symbols_unlocked(self, symbols: list[dict]) -> None:
        if not symbols:
            return
        self._conn.executemany(
            """
            INSERT INTO code_symbols (id, repo_id, user_id, kind, name, file_path, start_line, end_line, language)
            VALUES (:id, :repo_id, :user_id, :kind, :name, :file_path, :start_line, :end_line, :language)
            ON CONFLICT (id) DO UPDATE SET
                repo_id = excluded.repo_id, user_id = excluded.user_id, kind = excluded.kind,
                name = excluded.name, file_path = excluded.file_path, start_line = excluded.start_line,
                end_line = excluded.end_line, language = excluded.language
            """,
            symbols,
        )

    def _upsert_code_edges_unlocked(self, edges: list[dict]) -> None:
        if not edges:
            return
        rows = []
        for e in edges:
            if e["type"] not in _CODE_EDGE_TYPES:
                raise ValueError(f"unknown code edge type: {e['type']}")
            rows.append((e["source"], e["target"], e["type"]))
        self._conn.executemany(
            "INSERT OR IGNORE INTO code_edges (source, target, type) VALUES (?, ?, ?)", rows
        )

    def _delete_repo_unlocked(self, user_id: str, repo_id: str) -> None:
        ids = [
            row["id"] for row in self._conn.execute(
                "SELECT id FROM code_symbols WHERE user_id = ? AND repo_id = ?", (user_id, repo_id)
            ).fetchall()
        ]
        self._conn.execute("DELETE FROM code_symbols WHERE user_id = ? AND repo_id = ?", (user_id, repo_id))
        if ids:
            placeholders = _in_clause(len(ids))
            self._conn.execute(
                f"DELETE FROM code_edges WHERE source IN {placeholders} OR target IN {placeholders}", ids + ids
            )
            self._conn.execute(f"DELETE FROM mentions_edges WHERE target IN {placeholders}", ids)
        self._conn.execute("DELETE FROM repos WHERE user_id = ? AND repo_id = ?", (user_id, repo_id))

    # --- GraphStore interface ---

    def upsert_repo(self, repo: dict) -> None:
        with self._lock, self._conn:
            self._upsert_repo_unlocked(repo)

    def upsert_symbols(self, symbols: list[dict]) -> None:
        with self._lock, self._conn:
            self._upsert_symbols_unlocked(symbols)

    def upsert_code_edges(self, edges: list[dict]) -> None:
        with self._lock, self._conn:
            self._upsert_code_edges_unlocked(edges)

    def delete_repo(self, user_id: str, repo_id: str) -> None:
        with self._lock, self._conn:
            self._delete_repo_unlocked(user_id, repo_id)

    def replace_repo_graph(self, repo: dict, symbols: list[dict], edges: list[dict]) -> None:
        with self._lock, self._conn:
            self._delete_repo_unlocked(repo["user_id"], repo["repo_id"])
            self._upsert_repo_unlocked(repo)
            self._upsert_symbols_unlocked(symbols)
            self._upsert_code_edges_unlocked(edges)

    def get_repo(self, user_id: str, repo_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, repo_id, source, local_path, last_indexed_at "
                "FROM repos WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchone()
        return dict(row) if row else None

    def list_repos(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, repo_id, source, local_path, last_indexed_at FROM repos"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_subgraph(self, user_id: str, repo_id: str) -> tuple[list[dict], list[dict]]:
        with self._lock:
            symbol_rows = self._conn.execute(
                "SELECT id, repo_id, user_id, kind, name, file_path, start_line, end_line, language "
                "FROM code_symbols WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchall()
            nodes_by_id: dict[str, dict] = {row["id"]: dict(row) for row in symbol_rows}
            ids = list(nodes_by_id.keys())

            edges: list[dict] = []
            if ids:
                placeholders = _in_clause(len(ids))
                code_edge_rows = self._conn.execute(
                    f"SELECT source, target, type FROM code_edges "
                    f"WHERE source IN {placeholders} AND target IN {placeholders}",
                    ids + ids,
                ).fetchall()
                edges.extend(dict(row) for row in code_edge_rows)

                mention_rows = self._conn.execute(
                    f"SELECT source, target FROM mentions_edges WHERE target IN {placeholders}", ids
                ).fetchall()
                mention_source_ids = [row["source"] for row in mention_rows]
                if mention_source_ids:
                    te_placeholders = _in_clause(len(mention_source_ids))
                    te_rows = self._conn.execute(
                        f"SELECT id, user_id, name, entity_type, source_doc_id, source_memory_id "
                        f"FROM text_entities WHERE id IN {te_placeholders}",
                        mention_source_ids,
                    ).fetchall()
                    for row in te_rows:
                        nodes_by_id[row["id"]] = dict(row)
                for row in mention_rows:
                    edges.append({"source": row["source"], "target": row["target"], "type": "MENTIONS"})

        return list(nodes_by_id.values()), edges

    def ping(self) -> bool:
        with self._lock:
            self._conn.execute("SELECT 1")
        return True

    def upsert_text_entities(self, entities: list[dict]) -> None:
        if not entities:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                """
                INSERT INTO text_entities (id, user_id, name, entity_type, source_doc_id, source_memory_id)
                VALUES (:id, :user_id, :name, :entity_type, :source_doc_id, :source_memory_id)
                ON CONFLICT (id) DO UPDATE SET
                    user_id = excluded.user_id, name = excluded.name, entity_type = excluded.entity_type,
                    source_doc_id = excluded.source_doc_id, source_memory_id = excluded.source_memory_id
                """,
                entities,
            )

    def upsert_related_edges(self, edges: list[dict]) -> None:
        if not edges:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO related_edges (source, target) VALUES (:source, :target)", edges
            )

    def upsert_mentions_edges(self, edges: list[dict]) -> None:
        if not edges:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO mentions_edges (source, target) VALUES (:source, :target)", edges
            )

    def list_text_entities(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, user_id, name, entity_type, source_doc_id, source_memory_id "
                "FROM text_entities WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_code_symbols(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, repo_id, user_id, kind, name, file_path, start_line, end_line, language "
                "FROM code_symbols WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_text_entities_by_source_doc(self, user_id: str, source_doc_id: str) -> None:
        with self._lock, self._conn:
            ids = [
                row["id"] for row in self._conn.execute(
                    "SELECT id FROM text_entities WHERE user_id = ? AND source_doc_id = ?",
                    (user_id, source_doc_id),
                ).fetchall()
            ]
            self._conn.execute(
                "DELETE FROM text_entities WHERE user_id = ? AND source_doc_id = ?", (user_id, source_doc_id)
            )
            if ids:
                placeholders = _in_clause(len(ids))
                self._conn.execute(
                    f"DELETE FROM related_edges WHERE source IN {placeholders} OR target IN {placeholders}",
                    ids + ids,
                )
                self._conn.execute(f"DELETE FROM mentions_edges WHERE source IN {placeholders}", ids)


@lru_cache
def get_graph_store() -> GraphStore:
    settings = get_settings()
    if settings.deploy_mode == "local":
        os.makedirs(settings.local_data_dir, exist_ok=True)
        return SqliteGraphStore(os.path.join(settings.local_data_dir, "graph.sqlite"))
    driver = GraphDatabase.driver(
        settings.neo4j_url, auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value())
    )
    return Neo4jGraphStore(driver)
