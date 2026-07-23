# Zero-Service Deploy Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `DEPLOY_MODE=local` alternative to the Qdrant/Neo4j/Postgres docker stack so hoton-graphtr can run as a single process with zero external services, while `DEPLOY_MODE=server` (default) keeps today's behavior byte-for-byte unchanged.

**Architecture:** One `DEPLOY_MODE` setting gates three independent factory functions (`get_qdrant_client`, `get_graph_store`, `get_usage_store`). In `local` mode: Qdrant uses its built-in embedded/on-disk mode (`QdrantClient(path=...)`), and two new classes — `SqliteGraphStore` and `SqliteUsageStore` — implement the existing `GraphStore`/`UsageStore` ABCs on top of stdlib `sqlite3`. All files land under `graphtr-out/`. No other module changes, since everything else in the app only depends on the `GraphStore`/`UsageStore`/`QdrantClient` interfaces.

**Tech Stack:** Python stdlib `sqlite3` (no new dependency), `qdrant-client`'s built-in local mode (no new dependency).

Spec: `docs/superpowers/specs/2026-07-23-zero-service-deploy-mode-design.md`

---

### Task 1: `DEPLOY_MODE` / `LOCAL_DATA_DIR` settings

**Files:**
- Modify: `app/config.py:30` (insert after `dashboard_password`)
- Test: `tests/test_config.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_settings_deploy_mode_defaults(monkeypatch):
    monkeypatch.delenv("DEPLOY_MODE", raising=False)
    monkeypatch.delenv("LOCAL_DATA_DIR", raising=False)
    settings = Settings()
    assert settings.deploy_mode == "server"
    assert settings.local_data_dir == "./graphtr-out"


def test_settings_reads_deploy_mode_env(monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", "/tmp/graphtr-data")
    settings = Settings()
    assert settings.deploy_mode == "local"
    assert settings.local_data_dir == "/tmp/graphtr-data"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v -k deploy_mode`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'deploy_mode'`

- [ ] **Step 3: Add the settings**

In `app/config.py`, insert after line 30 (`dashboard_password: SecretStr = SecretStr("")`), before the blank line and `class Config:`:

```python
    deploy_mode: str = "server"          # "server" (default, Qdrant/Neo4j/Postgres) | "local" (zero-service, file-backed)
    local_data_dir: str = "./graphtr-out"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v -k deploy_mode`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat(config): add DEPLOY_MODE/LOCAL_DATA_DIR settings for zero-service mode"
```

---

### Task 2: Qdrant embedded/local-path mode

**Files:**
- Modify: `app/clients/qdrant_store.py:1-9,61-66`
- Test: `tests/test_qdrant_store.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_qdrant_store.py`:

```python
def test_bootstrap_collections_works_with_local_path_client(tmp_path):
    from qdrant_client import QdrantClient
    from app.clients.qdrant_store import bootstrap_collections

    client = QdrantClient(path=str(tmp_path / "qdrant"))
    bootstrap_collections(client, embed_dim=384)

    names = {c.name for c in client.get_collections().collections}
    assert {"rag_documents", "rag_chunks", "user_memories"}.issubset(names)


def test_get_qdrant_client_uses_local_path_in_local_deploy_mode(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.clients.qdrant_store import get_qdrant_client

    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_qdrant_client.cache_clear()

    client = get_qdrant_client()

    names = {c.name for c in client.get_collections().collections}
    assert "rag_chunks" in names
    assert (tmp_path / "qdrant").is_dir()

    get_qdrant_client.cache_clear()
    get_settings.cache_clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_qdrant_store.py -v -k "local_path or local_deploy_mode"`
Expected: FAIL — `test_get_qdrant_client_uses_local_path_in_local_deploy_mode` fails with a connection error (tries to reach `http://localhost:6333`, since `deploy_mode` isn't wired in yet). `test_bootstrap_collections_works_with_local_path_client` should already PASS (it only exercises `bootstrap_collections`, which doesn't care how the client was constructed) — confirming `QdrantClient(path=...)` itself works before touching app code.

- [ ] **Step 3: Wire `deploy_mode` into `get_qdrant_client`**

In `app/clients/qdrant_store.py`, add `import os` as the first line:

```python
import os
from functools import lru_cache
```

Replace lines 61-66 (`get_qdrant_client`):

```python
@lru_cache
def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    if settings.deploy_mode == "local":
        os.makedirs(settings.local_data_dir, exist_ok=True)
        client = QdrantClient(path=os.path.join(settings.local_data_dir, "qdrant"))
    else:
        client = QdrantClient(url=settings.qdrant_url)
    bootstrap_collections(client, embed_dim=settings.embed_dim)
    return client
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_qdrant_store.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add app/clients/qdrant_store.py tests/test_qdrant_store.py
git commit -m "feat(qdrant): use embedded local-path client in DEPLOY_MODE=local"
```

---

### Task 3: `SqliteGraphStore` — implement + contract tests

**Files:**
- Modify: `app/graph/code_graph_store.py:1-18` (imports/header), append class after line 296
- Test: `tests/test_sqlite_graph_store.py` (new)

This mirrors the existing `GraphStore` contract already exercised against `FakeGraphStore` in `tests/test_code_graph_store.py`, run instead against a real `SqliteGraphStore` backed by a temp file — plus one test proving `replace_repo_graph` is atomic (a mid-transaction failure rolls back everything, not just the failing statement).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sqlite_graph_store.py`:

```python
import pytest

from app.graph.code_graph_store import SqliteGraphStore


@pytest.fixture
def sqlite_store(tmp_path):
    return SqliteGraphStore(str(tmp_path / "graph.sqlite"))


def test_sqlite_graph_store_ping_returns_true(sqlite_store):
    assert sqlite_store.ping() is True


def test_sqlite_upsert_and_get_subgraph_scoped_by_user_and_repo(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "b", "user_id": "u1", "repo_id": "r2", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    sqlite_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")

    assert [n["name"] for n in nodes] == ["foo"]
    assert edges == []  # b is in a different repo, so the cross-repo edge is excluded


def test_sqlite_upsert_symbols_is_idempotent(sqlite_store):
    symbol = {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
              "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"}
    sqlite_store.upsert_symbols([symbol])
    sqlite_store.upsert_symbols([{**symbol, "name": "foo_renamed"}])

    nodes, _ = sqlite_store.get_subgraph("u1", "r1")

    assert [n["name"] for n in nodes] == ["foo_renamed"]


def test_sqlite_delete_repo_removes_its_symbols_and_edges(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "c", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "baz",
         "file_path": "c.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    sqlite_store.upsert_code_edges([{"source": "a", "target": "c", "type": "CALLS"}])

    sqlite_store.delete_repo("u1", "r1")

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")
    assert nodes == []
    assert edges == []


def test_sqlite_get_subgraph_includes_text_entities_that_mention_a_symbol(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 10, "language": "python"},
    ])
    sqlite_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])
    sqlite_store.upsert_mentions_edges([{"source": "e1", "target": "s1"}])

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")

    assert {n["id"] for n in nodes} == {"s1", "e1"}
    assert {"source": "e1", "target": "s1", "type": "MENTIONS"} in edges


def test_sqlite_list_repos_returns_all_repos_across_users(sqlite_store):
    sqlite_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                               "local_path": "/tmp/r1", "last_indexed_at": "2026-07-22T00:00:00"})
    sqlite_store.upsert_repo({"user_id": "u2", "repo_id": "r2", "source": "/tmp/r2",
                               "local_path": "/tmp/r2", "last_indexed_at": "2026-07-22T00:00:00"})

    repos = sqlite_store.list_repos()

    assert {r["repo_id"] for r in repos} == {"r1", "r2"}


def test_sqlite_get_repo_returns_none_when_missing(sqlite_store):
    assert sqlite_store.get_repo("u1", "does-not-exist") is None


def test_sqlite_delete_text_entities_by_source_doc_removes_entity_and_edges(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "s1", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Retriever",
         "file_path": "retrieval.py", "start_line": 1, "end_line": 10, "language": "python"},
    ])
    sqlite_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])
    sqlite_store.upsert_mentions_edges([{"source": "e1", "target": "s1"}])

    sqlite_store.delete_text_entities_by_source_doc("u1", "doc-1")

    assert sqlite_store.list_text_entities("u1") == []
    nodes, edges = sqlite_store.get_subgraph("u1", "r1")
    assert edges == []


def test_sqlite_replace_repo_graph_rolls_back_entirely_on_invalid_edge_type(sqlite_store):
    sqlite_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                               "local_path": "/tmp/r1", "last_indexed_at": "2026-07-22T00:00:00"})
    sqlite_store.upsert_symbols([
        {"id": "old", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "old_fn",
         "file_path": "old.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])

    with pytest.raises(ValueError, match="unknown code edge type"):
        sqlite_store.replace_repo_graph(
            {"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
             "local_path": "/tmp/r1", "last_indexed_at": "2026-07-22T01:00:00"},
            [{"id": "new", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "new_fn",
              "file_path": "new.py", "start_line": 1, "end_line": 2, "language": "python"}],
            [{"source": "new", "target": "new", "type": "NOT_A_REAL_TYPE"}],
        )

    # the whole replace must have rolled back -- old data still intact, new data absent
    nodes, _ = sqlite_store.get_subgraph("u1", "r1")
    assert [n["name"] for n in nodes] == ["old_fn"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sqlite_graph_store.py -v`
Expected: FAIL on collection — `ImportError: cannot import name 'SqliteGraphStore' from 'app.graph.code_graph_store'`

- [ ] **Step 3: Implement `SqliteGraphStore`**

In `app/graph/code_graph_store.py`, replace the import block (lines 7-12) with:

```python
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from functools import lru_cache

from neo4j import GraphDatabase

from app.config import get_settings
```

Then insert the following class after `Neo4jGraphStore` ends (after line 296, i.e. right before the blank line preceding `@lru_cache` / `def get_graph_store()`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sqlite_graph_store.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add app/graph/code_graph_store.py tests/test_sqlite_graph_store.py
git commit -m "feat(graph): add SqliteGraphStore for DEPLOY_MODE=local"
```

---

### Task 4: `get_graph_store()` factory branch

**Files:**
- Modify: `app/graph/code_graph_store.py:299-305` (the `get_graph_store` factory, now shifted down by the Task 3 insertion — locate by function name, not line number)
- Test: `tests/test_sqlite_graph_store.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sqlite_graph_store.py`:

```python
def test_get_graph_store_returns_sqlite_store_in_local_deploy_mode(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.graph.code_graph_store import get_graph_store

    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_graph_store.cache_clear()

    store = get_graph_store()

    assert isinstance(store, SqliteGraphStore)
    assert (tmp_path / "graph.sqlite").exists()

    get_graph_store.cache_clear()
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sqlite_graph_store.py -v -k local_deploy_mode`
Expected: FAIL — attempts a real `bolt://localhost:7687` connection and errors, since `get_graph_store()` doesn't check `deploy_mode` yet.

- [ ] **Step 3: Branch the factory on `deploy_mode`**

In `app/graph/code_graph_store.py`, replace the `get_graph_store` function:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sqlite_graph_store.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add app/graph/code_graph_store.py tests/test_sqlite_graph_store.py
git commit -m "feat(graph): get_graph_store() returns SqliteGraphStore in DEPLOY_MODE=local"
```

---

### Task 5: `SqliteUsageStore` — implement + contract tests

**Files:**
- Modify: `app/dashboard/usage_store.py:1-16` (imports), append class after line 170
- Test: `tests/test_sqlite_usage_store.py` (new)

Mirrors `tests/test_usage_store.py`'s `FakeUsageStore` cases, run against a real `SqliteUsageStore`. SQLite has no `percentile_cont()`, so p50 is computed the same way `FakeUsageStore` already does it: sort durations, take the middle element in Python.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sqlite_usage_store.py`:

```python
from datetime import datetime, timedelta, timezone

from app.dashboard.usage_store import SqliteUsageStore


def _store(tmp_path):
    return SqliteUsageStore(str(tmp_path / "usage.sqlite"))


def test_sqlite_usage_store_ping_returns_true(tmp_path):
    assert _store(tmp_path).ping() is True


def test_sqlite_usage_store_counts_by_tool(tmp_path):
    store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 10.0, "created_at": now,
    })
    store.record({
        "tool_name": "retrieve_chunks", "user_id": "u2", "repo_id": None,
        "success": False, "error_message": "boom", "duration_ms": 20.0, "created_at": now,
    })

    rows = store.counts_by_tool(since=now - timedelta(hours=1))

    assert rows == [{"tool_name": "retrieve_chunks", "calls": 2, "errors": 1, "p50_ms": 20.0}]


def test_sqlite_usage_store_counts_by_tool_excludes_events_before_since(tmp_path):
    store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    store.record({
        "tool_name": "retrieve_chunks", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 10.0, "created_at": now - timedelta(days=2),
    })

    rows = store.counts_by_tool(since=now - timedelta(hours=1))

    assert rows == []


def test_sqlite_usage_store_counts_by_user(tmp_path):
    store = _store(tmp_path)
    now = datetime.now(timezone.utc)
    store.record({
        "tool_name": "get_rag_context", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })
    store.record({
        "tool_name": "query_code_graph", "user_id": "u1", "repo_id": "r1",
        "success": True, "error_message": None, "duration_ms": 5.0, "created_at": now,
    })

    rows = store.counts_by_user(since=now - timedelta(hours=1))

    assert rows == [{"user_id": "u1", "calls": 2}]


def test_sqlite_usage_store_persists_across_reconnect(tmp_path):
    db_path = str(tmp_path / "usage.sqlite")
    now = datetime.now(timezone.utc)
    SqliteUsageStore(db_path).record({
        "tool_name": "embed_text", "user_id": "u1", "repo_id": None,
        "success": True, "error_message": None, "duration_ms": 1.0, "created_at": now,
    })

    rows = SqliteUsageStore(db_path).counts_by_tool(since=now - timedelta(hours=1))

    assert rows == [{"tool_name": "embed_text", "calls": 1, "errors": 0, "p50_ms": 1.0}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sqlite_usage_store.py -v`
Expected: FAIL on collection — `ImportError: cannot import name 'SqliteUsageStore' from 'app.dashboard.usage_store'`

- [ ] **Step 3: Implement `SqliteUsageStore`**

In `app/dashboard/usage_store.py`, replace the import block (lines 7-15) with:

```python
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlparse, urlunparse

import psycopg

from app.config import get_settings
```

Then insert the following class after `PostgresUsageStore` ends (after line 170, right before the blank line preceding `@lru_cache` / `def get_usage_store()`):

```python
class SqliteUsageStore(UsageStore):
    """File-backed UsageStore for DEPLOY_MODE=local. p50 is computed in
    Python (sorted durations, middle element) since SQLite has no
    percentile_cont() — same approach FakeUsageStore already uses in
    tests/conftest.py."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    repo_id TEXT,
                    success INTEGER NOT NULL,
                    error_message TEXT,
                    duration_ms REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS usage_events_tool_time_idx ON usage_events (tool_name, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS usage_events_user_time_idx ON usage_events (user_id, created_at)"
            )

    def record(self, event: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO usage_events (tool_name, user_id, repo_id, success, error_message, duration_ms, created_at)
                VALUES (:tool_name, :user_id, :repo_id, :success, :error_message, :duration_ms, :created_at)
                """,
                {**event, "success": int(event["success"]), "created_at": event["created_at"].isoformat()},
            )

    def counts_by_tool(self, since: datetime) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tool_name, success, duration_ms FROM usage_events WHERE created_at >= ?",
                (since.isoformat(),),
            ).fetchall()

        by_tool: dict[str, dict] = {}
        for row in rows:
            entry = by_tool.setdefault(
                row["tool_name"], {"tool_name": row["tool_name"], "calls": 0, "errors": 0, "durations": []}
            )
            entry["calls"] += 1
            if not row["success"]:
                entry["errors"] += 1
            entry["durations"].append(row["duration_ms"])

        result = []
        for entry in by_tool.values():
            durations = sorted(entry.pop("durations"))
            entry["p50_ms"] = durations[len(durations) // 2] if durations else 0.0
            result.append(entry)
        result.sort(key=lambda r: r["calls"], reverse=True)
        return result

    def counts_by_user(self, since: datetime) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, count(*) AS calls FROM usage_events "
                "WHERE created_at >= ? GROUP BY user_id ORDER BY calls DESC",
                (since.isoformat(),),
            ).fetchall()
        return [{"user_id": row["user_id"], "calls": row["calls"]} for row in rows]

    def ping(self) -> bool:
        with self._lock:
            self._conn.execute("SELECT 1")
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sqlite_usage_store.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/dashboard/usage_store.py tests/test_sqlite_usage_store.py
git commit -m "feat(dashboard): add SqliteUsageStore for DEPLOY_MODE=local"
```

---

### Task 6: `get_usage_store()` factory branch

**Files:**
- Modify: `app/dashboard/usage_store.py` (the `get_usage_store` factory — locate by function name, shifted down by Task 5's insertion)
- Test: `tests/test_sqlite_usage_store.py` (append)

Unlike the current optional-off behavior (no Postgres env vars set → `None`, dashboard tracking silently disabled), `local` mode always returns a working `SqliteUsageStore` — per the approved spec scope ("all 3"), `/dashboard` stays fully functional in zero-service mode.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sqlite_usage_store.py`:

```python
def test_get_usage_store_returns_sqlite_store_in_local_deploy_mode(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.dashboard.usage_store import get_usage_store

    monkeypatch.setenv("DEPLOY_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("USAGE_DB_HOST", raising=False)
    monkeypatch.delenv("USAGE_DB_URL", raising=False)
    get_settings.cache_clear()
    get_usage_store.cache_clear()

    store = get_usage_store()

    assert isinstance(store, SqliteUsageStore)
    assert (tmp_path / "usage.sqlite").exists()

    get_usage_store.cache_clear()
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sqlite_usage_store.py -v -k local_deploy_mode`
Expected: FAIL — `assert None is not None` (or `isinstance` failure), since with no `USAGE_DB_HOST`/`USAGE_DB_URL` set, `get_usage_store()` currently returns `None` regardless of `deploy_mode`.

- [ ] **Step 3: Branch the factory on `deploy_mode`**

In `app/dashboard/usage_store.py`, replace the `get_usage_store` function:

```python
@lru_cache
def get_usage_store() -> "UsageStore | None":
    settings = get_settings()
    if settings.deploy_mode == "local":
        os.makedirs(settings.local_data_dir, exist_ok=True)
        return SqliteUsageStore(os.path.join(settings.local_data_dir, "usage.sqlite"))
    if settings.usage_db_host:
        url = build_usage_db_url(
            settings.usage_db_host, settings.usage_db_port,
            settings.usage_db_user, settings.usage_db_password.get_secret_value(), settings.usage_db_name,
        )
    elif settings.usage_db_url:
        url = settings.usage_db_url
    else:
        return None
    bootstrap_usage_database(url)
    return PostgresUsageStore(url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sqlite_usage_store.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/dashboard/usage_store.py tests/test_sqlite_usage_store.py
git commit -m "feat(dashboard): get_usage_store() returns SqliteUsageStore in DEPLOY_MODE=local"
```

---

### Task 7: `.gitignore` + `docker/.env.example`

**Files:**
- Modify: `.gitignore:32-37`
- Modify: `docker/.env.example:1`

- [ ] **Step 1: Update `.gitignore`**

Replace lines 32-37 of `.gitignore`:

```
# Project runtime data
repos/
graphtr-out/*.json
graphtr-out/*.html
graphtr-out/*.sqlite
graphtr-out/qdrant/
docker/code-repos/
docker/models/
```

- [ ] **Step 2: Add `DEPLOY_MODE` to `docker/.env.example`**

Insert at the top of `docker/.env.example` (before `QDRANT_URL=...`):

```
# "server" (default) uses Qdrant/Neo4j/Postgres below. "local" runs
# zero-service: writes to LOCAL_DATA_DIR via qdrant-client's embedded mode +
# SQLite, and ignores QDRANT_URL/NEO4J_*/USAGE_DB_* entirely.
DEPLOY_MODE=server
LOCAL_DATA_DIR=./graphtr-out
```

- [ ] **Step 3: Verify no tracked file matches the new ignore patterns**

Run: `git status --porcelain`
Expected: no `graphtr-out/*.sqlite` or `graphtr-out/qdrant/` paths listed as tracked-and-modified (there should be none, since these files don't exist yet).

- [ ] **Step 4: Commit**

```bash
git add .gitignore docker/.env.example
git commit -m "chore: gitignore local-mode data files, document DEPLOY_MODE in .env.example"
```

---

### Task 8: README zero-service section

**Files:**
- Modify: `README.md:36-42` (the `### Local` subsection under `## Run`)

- [ ] **Step 1: Add the new subsection**

In `README.md`, after the existing `### Local` subsection (ending at line 42 with the `uvicorn` command block) and before `## Tests` (line 44), insert:

```markdown

### Zero-service (no Docker, no external DB)

```bash
pip install -r requirements.txt
DEPLOY_MODE=local uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030
```

No Qdrant/Neo4j/Postgres needed — vectors, the code graph, and usage tracking
all write to `graphtr-out/` (`qdrant/`, `graph.sqlite`, `usage.sqlite`).
`server` and `local` are two independent data stores, not a live migration
path — switching `DEPLOY_MODE` does not carry data over.
```

- [ ] **Step 2: Confirm the file renders correctly**

Run: `grep -n "Zero-service" README.md`
Expected: one match, under the `## Run` section.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document DEPLOY_MODE=local zero-service setup"
```

---

### Task 9: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q tests/`
Expected: all tests pass, including every test added in Tasks 1-6, with no regressions in the existing suite (Neo4j/Postgres integration tests remain skipped, as they require `NEO4J_TEST_URL`/`USAGE_DB_TEST_URL`).

- [ ] **Step 2: Manually smoke-test zero-service mode**

Run:
```bash
rm -rf /tmp/graphtr-smoke
DEPLOY_MODE=local LOCAL_DATA_DIR=/tmp/graphtr-smoke \
  python -c "
from app.clients.qdrant_store import get_qdrant_client
from app.graph.code_graph_store import get_graph_store
from app.dashboard.usage_store import get_usage_store

get_qdrant_client()
get_graph_store()
get_usage_store()
print('ok')
"
ls /tmp/graphtr-smoke
```
Expected: prints `ok`, and `/tmp/graphtr-smoke` contains `qdrant/`, `graph.sqlite`, `usage.sqlite`.

- [ ] **Step 3: Confirm `DEPLOY_MODE=server` (default) is unaffected**

Run: `pytest -q tests/test_qdrant_store.py tests/test_config.py tests/test_sqlite_graph_store.py tests/test_sqlite_usage_store.py -v`
Expected: all PASS — the default-mode tests (that don't set `DEPLOY_MODE`) exercise the unchanged `server`-mode code paths and still pass.

- [ ] **Step 4: Final commit (if any cleanup was needed)**

```bash
git status
```
Expected: working tree clean — every task already committed its own changes. If `/tmp/graphtr-smoke` or other stray files show up, they're outside the repo and don't need committing.
