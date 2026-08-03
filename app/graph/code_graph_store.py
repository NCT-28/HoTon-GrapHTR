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
    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        """Incremental variant of replace_repo_graph: only symbols/edges whose file_path is
        in `stale_file_paths` are deleted before `symbols`/`edges` are inserted -- every
        other row already stored for this repo is left untouched. Same no-partial-state
        atomicity contract as replace_repo_graph, scoped to `stale_file_paths` instead of
        the whole repo."""
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
                n.end_line = s.end_line, n.language = s.language, n.content_hash = s.content_hash
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
                        n.end_line = s.end_line, n.language = s.language, n.content_hash = s.content_hash
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

    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        user_id, repo_id = repo["user_id"], repo["repo_id"]

        def _work(tx):
            if stale_file_paths:
                tx.run(
                    "MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id}) "
                    "WHERE n.file_path IN $file_paths DETACH DELETE n",
                    user_id=user_id, repo_id=repo_id, file_paths=stale_file_paths,
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
                        n.end_line = s.end_line, n.language = s.language, n.content_hash = s.content_hash
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


def _chunked(items: list[str], size: int = 900):
    """Yield `items` in batches small enough to bind as SQL parameters. Used only
    where the ids come from another database (LocalMultiRepoGraphStore's per-repo
    stores) and so can't be expressed as a correlated subquery. The real ceiling is
    a compile-time constant that varies by build (999 before SQLite 3.32, 32766 by
    default after, higher in some distributions), so 900 stays under all of them."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


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
        # Many existing callers (tests, older code) build symbol dicts without
        # content_hash -- default to None rather than requiring every one of them
        # to add the new field just to keep working.
        rows = [{**s, "content_hash": s.get("content_hash")} for s in symbols]
        self._conn.executemany(
            """
            INSERT INTO code_symbols
                (id, repo_id, user_id, kind, name, file_path, start_line, end_line, language, content_hash)
            VALUES
                (:id, :repo_id, :user_id, :kind, :name, :file_path, :start_line, :end_line, :language, :content_hash)
            ON CONFLICT (id) DO UPDATE SET
                repo_id = excluded.repo_id, user_id = excluded.user_id, kind = excluded.kind,
                name = excluded.name, file_path = excluded.file_path, start_line = excluded.start_line,
                end_line = excluded.end_line, language = excluded.language, content_hash = excluded.content_hash
            """,
            rows,
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
        # Edge deletes run BEFORE the symbol delete: they name the symbols via a
        # correlated subquery, so the rows have to still be there. Constant
        # parameter count regardless of repo size (was one param per symbol id,
        # which eventually blew past SQLITE_LIMIT_VARIABLE_NUMBER). The
        # source/target predicates are separate statements rather than one OR'd
        # statement so each can use an index (see code_edges_target_idx).
        scope = "(SELECT id FROM code_symbols WHERE user_id = ? AND repo_id = ?)"
        self._conn.execute(f"DELETE FROM code_edges WHERE source IN {scope}", (user_id, repo_id))
        self._conn.execute(f"DELETE FROM code_edges WHERE target IN {scope}", (user_id, repo_id))
        self._conn.execute(f"DELETE FROM mentions_edges WHERE target IN {scope}", (user_id, repo_id))
        self._conn.execute("DELETE FROM code_symbols WHERE user_id = ? AND repo_id = ?", (user_id, repo_id))
        self._conn.execute("DELETE FROM repos WHERE user_id = ? AND repo_id = ?", (user_id, repo_id))

    def _delete_files_unlocked(self, user_id: str, repo_id: str, file_paths: list[str]) -> None:
        if not file_paths:
            return
        # Same edges-before-symbols ordering and correlated-subquery scoping as
        # _delete_repo_unlocked, narrowed to the stale files. Parameter count is
        # 2 + len(file_paths) -- bounded by one debounce window's changed files,
        # not by repo size.
        file_placeholders = _in_clause(len(file_paths))
        scope = (
            f"(SELECT id FROM code_symbols WHERE user_id = ? AND repo_id = ? "
            f"AND file_path IN {file_placeholders})"
        )
        scope_params = [user_id, repo_id] + file_paths
        self._conn.execute(f"DELETE FROM code_edges WHERE source IN {scope}", scope_params)
        self._conn.execute(f"DELETE FROM code_edges WHERE target IN {scope}", scope_params)
        self._conn.execute(f"DELETE FROM mentions_edges WHERE target IN {scope}", scope_params)
        self._conn.execute(
            f"DELETE FROM code_symbols WHERE user_id = ? AND repo_id = ? AND file_path IN {file_placeholders}",
            scope_params,
        )

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

    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        with self._lock, self._conn:
            self._delete_files_unlocked(repo["user_id"], repo["repo_id"], stale_file_paths)
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
        # Every id-list predicate here is a correlated subquery rather than an
        # IN (?,?,...) over the repo's symbol ids -- the parameter count is
        # constant instead of scaling with repo size.
        scope = "(SELECT id FROM code_symbols WHERE user_id = ? AND repo_id = ?)"
        with self._lock:
            symbol_rows = self._conn.execute(
                "SELECT id, repo_id, user_id, kind, name, file_path, start_line, end_line, language, content_hash "
                "FROM code_symbols WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchall()
            nodes_by_id: dict[str, dict] = {row["id"]: dict(row) for row in symbol_rows}

            edges: list[dict] = []
            if nodes_by_id:
                code_edge_rows = self._conn.execute(
                    f"SELECT source, target, type FROM code_edges "
                    f"WHERE source IN {scope} AND target IN {scope}",
                    (user_id, repo_id, user_id, repo_id),
                ).fetchall()
                edges.extend(dict(row) for row in code_edge_rows)

                mention_rows = self._conn.execute(
                    f"SELECT source, target FROM mentions_edges WHERE target IN {scope}",
                    (user_id, repo_id),
                ).fetchall()
                if mention_rows:
                    te_rows = self._conn.execute(
                        f"SELECT id, user_id, name, entity_type, source_doc_id, source_memory_id "
                        f"FROM text_entities "
                        f"WHERE id IN (SELECT source FROM mentions_edges WHERE target IN {scope})",
                        (user_id, repo_id),
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

    def get_mentioning_text_entities(self, symbol_ids: list[str]) -> tuple[list[dict], list[dict]]:
        """Text-entity nodes and MENTIONS edges targeting any of `symbol_ids`. Used by
        LocalMultiRepoGraphStore to fuse this (central) store's text entities onto code
        symbols that live in a separate per-repo store's get_subgraph() result.

        `symbol_ids` comes from a different database, so it can't be a correlated
        subquery like the rest of this class -- it's chunked instead to keep the bound
        parameter count under SQLITE_LIMIT_VARIABLE_NUMBER."""
        if not symbol_ids:
            return [], []
        mention_rows: list[dict] = []
        entities_by_id: dict[str, dict] = {}
        with self._lock:
            for chunk in _chunked(symbol_ids):
                placeholders = _in_clause(len(chunk))
                rows = self._conn.execute(
                    f"SELECT source, target FROM mentions_edges WHERE target IN {placeholders}", chunk
                ).fetchall()
                mention_rows.extend({"source": r["source"], "target": r["target"]} for r in rows)
            source_ids = list({r["source"] for r in mention_rows})
            for chunk in _chunked(source_ids):
                placeholders = _in_clause(len(chunk))
                te_rows = self._conn.execute(
                    f"SELECT id, user_id, name, entity_type, source_doc_id, source_memory_id "
                    f"FROM text_entities WHERE id IN {placeholders}",
                    chunk,
                ).fetchall()
                for row in te_rows:
                    entities_by_id[row["id"]] = dict(row)
        edges = [{"source": r["source"], "target": r["target"], "type": "MENTIONS"} for r in mention_rows]
        return list(entities_by_id.values()), edges

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


class LocalMultiRepoGraphStore(GraphStore):
    """DEPLOY_MODE=local GraphStore that keeps each repo's code symbols/edges in its own
    SqliteGraphStore under <repo local_path>/graphtr-out/graph.sqlite, instead of one file
    shared by every ingested repo -- so a large/many-repo install doesn't grow a single
    shared db under settings.local_data_dir without bound.

    A small central SqliteGraphStore (settings.local_data_dir/graph.sqlite) still holds the
    repo registry (used to route user_id/repo_id -> local_path) plus text entities and
    MENTIONS edges: those can reference code symbols in any of the user's repos, so they
    can't be split per-repo without breaking cross-repo entity linking."""

    def __init__(self, central_db_path: str):
        os.makedirs(os.path.dirname(central_db_path), exist_ok=True)
        self._central = SqliteGraphStore(central_db_path)
        self._repo_stores: dict[str, SqliteGraphStore] = {}
        self._repo_stores_lock = threading.Lock()

    def _repo_store(self, local_path: str) -> SqliteGraphStore:
        with self._repo_stores_lock:
            store = self._repo_stores.get(local_path)
            if store is None:
                repo_data_dir = os.path.join(local_path, "graphtr-out")
                os.makedirs(repo_data_dir, exist_ok=True)
                store = SqliteGraphStore(os.path.join(repo_data_dir, "graph.sqlite"))
                self._repo_stores[local_path] = store
            return store

    def _repo_store_for(self, user_id: str, repo_id: str) -> SqliteGraphStore | None:
        repo = self._central.get_repo(user_id, repo_id)
        return self._repo_store(repo["local_path"]) if repo is not None else None

    def upsert_repo(self, repo: dict) -> None:
        self._central.upsert_repo(repo)
        self._repo_store(repo["local_path"]).upsert_repo(repo)

    def upsert_symbols(self, symbols: list[dict]) -> None:
        by_repo: dict[tuple[str, str], list[dict]] = {}
        for s in symbols:
            by_repo.setdefault((s["user_id"], s["repo_id"]), []).append(s)
        for (user_id, repo_id), batch in by_repo.items():
            store = self._repo_store_for(user_id, repo_id)
            if store is not None:
                store.upsert_symbols(batch)

    def upsert_code_edges(self, edges: list[dict]) -> None:
        # code_edges rows carry only symbol ids, no repo_id -- fan out to every known
        # repo's store; get_subgraph's own source/target-in-this-repo's-symbols filter
        # keeps only the edges that actually belong to each repo.
        for repo in self._central.list_repos():
            self._repo_store(repo["local_path"]).upsert_code_edges(edges)

    def delete_repo(self, user_id: str, repo_id: str) -> None:
        repo = self._central.get_repo(user_id, repo_id)
        if repo is not None:
            self._repo_store(repo["local_path"]).delete_repo(user_id, repo_id)
        self._central.delete_repo(user_id, repo_id)

    def replace_repo_graph(self, repo: dict, symbols: list[dict], edges: list[dict]) -> None:
        self._central.upsert_repo(repo)
        self._repo_store(repo["local_path"]).replace_repo_graph(repo, symbols, edges)

    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        self._central.upsert_repo(repo)
        self._repo_store(repo["local_path"]).replace_files_in_repo(repo, stale_file_paths, symbols, edges)

    def get_repo(self, user_id: str, repo_id: str) -> dict | None:
        return self._central.get_repo(user_id, repo_id)

    def list_repos(self) -> list[dict]:
        return self._central.list_repos()

    def get_subgraph(self, user_id: str, repo_id: str) -> tuple[list[dict], list[dict]]:
        repo = self._central.get_repo(user_id, repo_id)
        if repo is None:
            return [], []
        nodes, edges = self._repo_store(repo["local_path"]).get_subgraph(user_id, repo_id)
        symbol_ids = [n["id"] for n in nodes]
        entity_nodes, mention_edges = self._central.get_mentioning_text_entities(symbol_ids)
        return nodes + entity_nodes, edges + mention_edges

    def ping(self) -> bool:
        return self._central.ping()

    def upsert_text_entities(self, entities: list[dict]) -> None:
        self._central.upsert_text_entities(entities)

    def upsert_related_edges(self, edges: list[dict]) -> None:
        self._central.upsert_related_edges(edges)

    def upsert_mentions_edges(self, edges: list[dict]) -> None:
        self._central.upsert_mentions_edges(edges)

    def list_text_entities(self, user_id: str) -> list[dict]:
        return self._central.list_text_entities(user_id)

    def list_code_symbols(self, user_id: str) -> list[dict]:
        symbols: list[dict] = []
        for repo in self._central.list_repos():
            if repo["user_id"] != user_id:
                continue
            symbols.extend(self._repo_store(repo["local_path"]).list_code_symbols(user_id))
        return symbols

    def delete_text_entities_by_source_doc(self, user_id: str, source_doc_id: str) -> None:
        self._central.delete_text_entities_by_source_doc(user_id, source_doc_id)


@lru_cache
def get_graph_store() -> GraphStore:
    settings = get_settings()
    if settings.deploy_mode == "local":
        os.makedirs(settings.local_data_dir, exist_ok=True)
        return LocalMultiRepoGraphStore(os.path.join(settings.local_data_dir, "graph.sqlite"))
    driver = GraphDatabase.driver(
        settings.neo4j_url, auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value())
    )
    return Neo4jGraphStore(driver)
