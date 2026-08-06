"""Graph storage interface for hoton-graphtr's text-entity graph, plus the
real Neo4j-backed implementation. `FakeGraphStore` (an in-memory test double
implementing the same interface) lives in tests/conftest.py so every consumer
of GraphStore can be unit-tested without a live Neo4j instance."""

import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from functools import lru_cache

from neo4j import GraphDatabase

from app.config import get_settings


class GraphStore(ABC):
    # --- text entities + cross-link ---

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

    @abstractmethod
    def ping(self) -> bool: ...


class Neo4jGraphStore(GraphStore):
    def __init__(self, driver):
        self._driver = driver

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
    """File-backed GraphStore for DEPLOY_MODE=local."""

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
                    language TEXT,
                    content_hash TEXT
                );
                CREATE INDEX IF NOT EXISTS code_symbols_scope_idx ON code_symbols (user_id, repo_id);
                CREATE INDEX IF NOT EXISTS code_symbols_file_idx ON code_symbols (user_id, repo_id, file_path);
                CREATE TABLE IF NOT EXISTS code_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    type TEXT NOT NULL,
                    UNIQUE (source, target, type)
                );
                CREATE INDEX IF NOT EXISTS code_edges_target_idx ON code_edges (target);
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
            # A db created before content_hash existed has the table but not the column
            # (CREATE TABLE IF NOT EXISTS above is then a no-op) -- sqlite has no
            # `ADD COLUMN IF NOT EXISTS`, so add it and swallow the "already exists"
            # error for dbs created fresh (which already have it from the CREATE TABLE).
            try:
                self._conn.execute("ALTER TABLE code_symbols ADD COLUMN content_hash TEXT")
            except sqlite3.OperationalError:
                pass

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
