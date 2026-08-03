# Graph Performance Fixes + Dashboard Fail-Closed Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the SQLite variable-limit scaling cliff and six other performance defects from the graph store / reindex / dashboard paths, and restore fail-closed HTTP Basic auth on the dashboard.

**Architecture:** Three themes. (1) SQLite queries that bind one parameter per symbol id are rewritten as correlated subqueries scoped by `user_id`/`repo_id`, so parameter count stops scaling with repo size; where a subquery is impossible (cross-database id lists in `LocalMultiRepoGraphStore`) the id list is chunked. (2) Two narrow read methods are added to the `GraphStore` interface — `list_symbol_index` (identity+diff fields only, for incremental reindex) and `count_subgraph` (aggregate counts, for the dashboard) — so callers stop materializing whole graphs to compute a diff or a `len()`. (3) `fuse_graph_context` builds its networkx graph once instead of once per keyword. Separately, `_require_auth` goes back to raising 503 when credentials are unconfigured.

**Tech Stack:** Python 3.10+, SQLite (stdlib `sqlite3`), Neo4j (`neo4j` driver), networkx, FastAPI, Qdrant (`qdrant-client`), pytest.

**Findings addressed:** P1 (SQLite variable-limit cliff), P2 (missing `code_edges(target)` index), P3 (per-keyword graph rebuild), P4 (full subgraph load per file save), P5 (dashboard materializes every repo graph), P6 (unpaginated Qdrant scroll), P7 (aggressive LLM idle-unload default), plus the dashboard fail-open auth finding.

**Out of scope (deliberate, decided with the user):** narrowing the `ReasoningLLM` inference lock (transformers pipelines are not reentrant; serializing is defensible), and Cypher-native BFS for `server` deploy mode.

---

## File Structure

| File | Change | Responsibility after the change |
|---|---|---|
| `app/graph/code_graph_store.py` | Modify | Adds `_chunked` helper, `code_edges(target)` index, correlated-subquery deletes/reads, and the `list_symbol_index` / `count_subgraph` interface methods across `GraphStore`, `Neo4jGraphStore`, `SqliteGraphStore`, `LocalMultiRepoGraphStore` |
| `app/graph/repo_watcher.py` | Modify (line 213) | Incremental reindex reads a narrow symbol index instead of the full subgraph |
| `app/graph/graph_query.py` | Modify | `_bfs_from_graph` core split out of `bfs_query`; `fuse_graph_context` builds the graph once |
| `app/dashboard/queries.py` | Modify | `project_breakdown` counts instead of materializing; `_count_by_user_id` paginates |
| `app/dashboard/router.py` | Modify | Fail-closed auth |
| `app/config.py` | Modify (line 12) | LLM idle-unload default 300 → 1800 |
| `tests/conftest.py` | Modify | `FakeGraphStore` implements the two new abstract methods |
| `tests/test_sqlite_graph_store.py` | Modify | Index presence, large-repo scaling, `list_symbol_index`, `count_subgraph` |
| `tests/test_local_multi_repo_graph_store.py` | Modify | `list_symbol_index` / `count_subgraph` routing across central + per-repo stores |
| `tests/test_code_graph_store_integration.py` | Modify | Neo4j `list_symbol_index` / `count_subgraph` (gated on `NEO4J_TEST_URL`) |
| `tests/test_repo_watcher.py` | Modify | Reindex reads the narrow index, not `get_subgraph` |
| `tests/test_graph_query.py` | Modify | `fuse_graph_context` builds the graph once |
| `tests/test_dashboard_queries.py` | Modify | `project_breakdown` uses counts; `_count_by_user_id` paginates past 10000 |
| `tests/test_dashboard_api.py` | Modify (line 46) | Unconfigured auth → 503 |
| `tests/test_config.py` | Modify | New idle-unload default |
| `CLAUDE.md`, `README.md` | Modify | Dashboard auth is fail-closed |

**Interface contract locked in here** (referenced by later tasks — names must match exactly):

```python
def list_symbol_index(self, user_id: str, repo_id: str) -> list[dict]: ...
    # each dict has exactly: id, name, kind, file_path, content_hash

def count_subgraph(self, user_id: str, repo_id: str) -> tuple[int, int]: ...
    # (node_count, edge_count) matching that backend's own get_subgraph() output
```

`SqliteGraphStore`-only helpers (not on the ABC, same as the existing `get_mentioning_text_entities`):

```python
def list_symbol_ids(self, user_id: str, repo_id: str) -> list[str]: ...
def count_mentioning_text_entities(self, symbol_ids: list[str]) -> tuple[int, int]: ...
    # (distinct text-entity count, MENTIONS edge count)
```

---

## Background an engineer new to this codebase needs

**Dual deploy mode.** Every stateful backend has two implementations picked at runtime by `Settings.deploy_mode`: `"server"` (Neo4j) and `"local"` (SQLite). Any change to the `GraphStore` interface must be made in **four** places: the `GraphStore` ABC and `Neo4jGraphStore` and `SqliteGraphStore` and `LocalMultiRepoGraphStore` in `app/graph/code_graph_store.py`, plus `FakeGraphStore` in `tests/conftest.py`. Adding an `@abstractmethod` without filling in all four makes every test that constructs a store fail with `TypeError: Can't instantiate abstract class`.

**`LocalMultiRepoGraphStore` splits storage.** A small *central* SQLite db (`graphtr-out/graph.sqlite`) holds the repo registry, text entities, and MENTIONS edges. Each repo's *own* symbols and code edges live in `<repo local_path>/graphtr-out/graph.sqlite`. So a query that needs both (like counting a subgraph including its mentioning text entities) has to talk to two databases, and symbol ids have to cross between them as a Python list — which is why chunking, not a subquery, is the fix there.

**SQLite parameter limit.** `SQLITE_MAX_VARIABLE_NUMBER` is 32766 on modern SQLite. `_in_clause(n)` emits `n` placeholders; several call sites pass `ids + ids` (2N params). Above ~16k symbols these raise `sqlite3.OperationalError: too many SQL variables`. That is the P1 cliff.

**Neo4j and SQLite `get_subgraph` differ slightly on MENTIONS.** SQLite appends a MENTIONS edge for every `mentions_edges` row whether or not a `text_entities` row exists for its source; Neo4j only produces one when a real `TextEntity` node matched. Each backend's `count_subgraph` must match **its own** `get_subgraph`, not the other's. This is called out again in Task 5.

**Running tests.** From the repo root: `pytest`. Neo4j integration tests skip automatically unless `NEO4J_TEST_URL` is set.

---

### Task 1: Index `code_edges(target)`

`code_edges` has only the implicit index from `UNIQUE (source, target, type)`. That index can serve `source IN (...)` (leading column) but not `target IN (...)`. Every delete path filters on both.

**Files:**
- Modify: `app/graph/code_graph_store.py:406-411` (the `code_edges` block inside `_init_schema`)
- Test: `tests/test_sqlite_graph_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sqlite_graph_store.py`:

```python
def test_sqlite_schema_indexes_code_edges_target(sqlite_store):
    # code_edges' UNIQUE (source, target, type) index can serve `source IN (...)`
    # but not `target IN (...)` -- the delete paths filter on both.
    rows = sqlite_store._conn.execute("PRAGMA index_list('code_edges')").fetchall()
    assert "code_edges_target_idx" in {row["name"] for row in rows}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sqlite_graph_store.py::test_sqlite_schema_indexes_code_edges_target -v`

Expected: FAIL with `AssertionError: assert 'code_edges_target_idx' in {'sqlite_autoindex_code_edges_1'}`

- [ ] **Step 3: Add the index to the schema script**

In `app/graph/code_graph_store.py`, inside `_init_schema`'s `executescript`, find:

```python
                CREATE TABLE IF NOT EXISTS code_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    type TEXT NOT NULL,
                    UNIQUE (source, target, type)
                );
```

Replace with:

```python
                CREATE TABLE IF NOT EXISTS code_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    type TEXT NOT NULL,
                    UNIQUE (source, target, type)
                );
                CREATE INDEX IF NOT EXISTS code_edges_target_idx ON code_edges (target);
```

No migration is needed: `_init_schema` runs its `executescript` on every construction and `CREATE INDEX IF NOT EXISTS` back-fills the index on pre-existing dbs.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sqlite_graph_store.py -v`

Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Verify the index back-fills onto a pre-existing db**

Append to `tests/test_sqlite_graph_store.py`:

```python
def test_sqlite_code_edges_target_index_backfills_on_pre_existing_db(tmp_path):
    import sqlite3

    from app.graph.code_graph_store import SqliteGraphStore

    db_path = str(tmp_path / "old.sqlite")
    # A db created before code_edges_target_idx existed: the table is there, the index isn't.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE code_edges (source TEXT NOT NULL, target TEXT NOT NULL, "
        "type TEXT NOT NULL, UNIQUE (source, target, type))"
    )
    conn.commit()
    conn.close()

    store = SqliteGraphStore(db_path)

    rows = store._conn.execute("PRAGMA index_list('code_edges')").fetchall()
    assert "code_edges_target_idx" in {row["name"] for row in rows}
```

Run: `pytest tests/test_sqlite_graph_store.py::test_sqlite_code_edges_target_index_backfills_on_pre_existing_db -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/graph/code_graph_store.py tests/test_sqlite_graph_store.py
git commit -m "perf(graph): index code_edges(target)

The only index on code_edges was the implicit one from UNIQUE (source,
target, type), which can serve a source-side lookup but not a target-side
one -- every delete path filters on both."
```

---

### Task 2: Stop binding one SQL parameter per symbol id

The P1 cliff. Four query sites in `SqliteGraphStore` bind `len(ids)` (or `2 * len(ids)`) parameters, where `ids` is every symbol in the repo. Three become correlated subqueries with a constant parameter count; the fourth (`get_mentioning_text_entities`, which receives an id list from a *different* database) gets chunked.

**Files:**
- Modify: `app/graph/code_graph_store.py:490-522` (`_delete_repo_unlocked`, `_delete_files_unlocked`), `:574-610` (`get_subgraph`), `:617-639` (`get_mentioning_text_entities`), and just below `:364` (`_in_clause`) for the new `_chunked` helper
- Test: `tests/test_sqlite_graph_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sqlite_graph_store.py`:

```python
def _many_symbols(count: int, user_id: str = "u1", repo_id: str = "r1") -> list[dict]:
    return [
        {"id": f"s{i}", "user_id": user_id, "repo_id": repo_id, "kind": "function",
         "name": f"fn{i}", "file_path": f"f{i}.py", "start_line": 1, "end_line": 2,
         "language": "python", "content_hash": f"h{i}"}
        for i in range(count)
    ]


def test_sqlite_get_subgraph_handles_more_symbols_than_the_sql_variable_limit(sqlite_store):
    # SQLITE_MAX_VARIABLE_NUMBER is 32766; get_subgraph used to bind 2 params per
    # symbol id, so a repo above ~16k symbols raised "too many SQL variables".
    sqlite_store.upsert_symbols(_many_symbols(20000))

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")

    assert len(nodes) == 20000
    assert edges == []


def test_sqlite_delete_repo_handles_more_symbols_than_the_sql_variable_limit(sqlite_store):
    sqlite_store.upsert_symbols(_many_symbols(20000))
    sqlite_store.upsert_code_edges([{"source": "s0", "target": "s1", "type": "CALLS"}])

    sqlite_store.delete_repo("u1", "r1")

    assert sqlite_store.get_subgraph("u1", "r1") == ([], [])
    assert sqlite_store._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0] == 0


def test_sqlite_get_mentioning_text_entities_handles_more_ids_than_the_sql_variable_limit(sqlite_store):
    sqlite_store.upsert_symbols(_many_symbols(20000))
    sqlite_store.upsert_text_entities([
        {"id": "te1", "user_id": "u1", "name": "Thing", "entity_type": "concept",
         "source_doc_id": "d1", "source_memory_id": None},
    ])
    sqlite_store.upsert_mentions_edges([{"source": "te1", "target": "s19999"}])

    entities, edges = sqlite_store.get_mentioning_text_entities([f"s{i}" for i in range(20000)])

    assert [e["id"] for e in entities] == ["te1"]
    assert edges == [{"source": "te1", "target": "s19999", "type": "MENTIONS"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sqlite_graph_store.py -k variable_limit -v`

Expected: 3 FAILs, each `sqlite3.OperationalError: too many SQL variables`

- [ ] **Step 3: Add the `_chunked` helper**

In `app/graph/code_graph_store.py`, directly after `_in_clause` (line 363-364), add:

```python
def _chunked(items: list[str], size: int = 900):
    """Yield `items` in batches small enough to bind as SQL parameters. Used only
    where the ids come from another database (LocalMultiRepoGraphStore's per-repo
    stores) and so can't be expressed as a correlated subquery. 900 stays under
    even the old 999-variable SQLite build limit."""
    for i in range(0, len(items), size):
        yield items[i : i + size]
```

- [ ] **Step 4: Rewrite `_delete_repo_unlocked` as scoped subqueries**

Replace the whole body of `_delete_repo_unlocked` (lines 490-503) with:

```python
    def _delete_repo_unlocked(self, user_id: str, repo_id: str) -> None:
        # Edge deletes run BEFORE the symbol delete: they name the symbols via a
        # correlated subquery, so the rows have to still be there. Constant
        # parameter count regardless of repo size (was one param per symbol id,
        # which blew past SQLITE_MAX_VARIABLE_NUMBER on large repos). The
        # source/target predicates are separate statements rather than one OR'd
        # statement so each can use an index (see code_edges_target_idx).
        scope = "(SELECT id FROM code_symbols WHERE user_id = ? AND repo_id = ?)"
        self._conn.execute(f"DELETE FROM code_edges WHERE source IN {scope}", (user_id, repo_id))
        self._conn.execute(f"DELETE FROM code_edges WHERE target IN {scope}", (user_id, repo_id))
        self._conn.execute(f"DELETE FROM mentions_edges WHERE target IN {scope}", (user_id, repo_id))
        self._conn.execute("DELETE FROM code_symbols WHERE user_id = ? AND repo_id = ?", (user_id, repo_id))
        self._conn.execute("DELETE FROM repos WHERE user_id = ? AND repo_id = ?", (user_id, repo_id))
```

- [ ] **Step 5: Rewrite `_delete_files_unlocked` the same way**

Replace the whole body of `_delete_files_unlocked` (lines 505-522) with:

```python
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
```

- [ ] **Step 6: Rewrite `get_subgraph`'s edge/entity queries as subqueries**

Replace the whole body of `get_subgraph` (lines 574-610) with:

```python
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
```

Note the preserved quirk: a MENTIONS edge is appended for every `mentions_edges` row even when no `text_entities` row exists for its source. That is existing behavior and Task 5's `count_subgraph` has to match it.

- [ ] **Step 7: Chunk `get_mentioning_text_entities`**

Replace the whole body of `get_mentioning_text_entities` (lines 617-639) with:

```python
    def get_mentioning_text_entities(self, symbol_ids: list[str]) -> tuple[list[dict], list[dict]]:
        """Text-entity nodes and MENTIONS edges targeting any of `symbol_ids`. Used by
        LocalMultiRepoGraphStore to fuse this (central) store's text entities onto code
        symbols that live in a separate per-repo store's get_subgraph() result.

        `symbol_ids` comes from a different database, so it can't be a correlated
        subquery like the rest of this class -- it's chunked instead to stay under
        SQLITE_MAX_VARIABLE_NUMBER."""
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
```

- [ ] **Step 8: Run the full sqlite store suite**

Run: `pytest tests/test_sqlite_graph_store.py tests/test_local_multi_repo_graph_store.py -v`

Expected: PASS, including the three new `variable_limit` tests. The pre-existing tests
(`test_sqlite_delete_repo_removes_its_symbols_and_edges`,
`test_sqlite_replace_files_in_repo_only_touches_symbols_in_stale_file_paths`,
`test_sqlite_replace_repo_graph_rolls_back_entirely_on_invalid_edge_type`,
`test_sqlite_get_subgraph_includes_text_entities_that_mention_a_symbol`) are the
regression guard that the rewritten SQL kept its semantics — they must still pass unchanged.

- [ ] **Step 9: Commit**

```bash
git add app/graph/code_graph_store.py tests/test_sqlite_graph_store.py
git commit -m "perf(graph): stop binding one SQL param per symbol id

get_subgraph and both delete paths bound len(ids) (or 2*len(ids))
parameters where ids was every symbol in the repo, so a repo above ~16k
symbols hit SQLITE_MAX_VARIABLE_NUMBER and raised 'too many SQL
variables'. Rewritten as correlated subqueries scoped by user_id/repo_id,
with a constant parameter count. get_mentioning_text_entities takes its
ids from another database and can't use a subquery, so it chunks instead."
```

---

### Task 3: Add `list_symbol_index` to the GraphStore interface

The narrow read that incremental reindex actually needs. Four implementations.

**Files:**
- Modify: `app/graph/code_graph_store.py` (ABC after `get_subgraph` at line 64; `Neo4jGraphStore` after its `get_subgraph`; `SqliteGraphStore` after its `get_subgraph`; `LocalMultiRepoGraphStore` after its `get_subgraph`)
- Modify: `tests/conftest.py` (`FakeGraphStore`, after `get_subgraph` at line 89)
- Test: `tests/test_sqlite_graph_store.py`, `tests/test_local_multi_repo_graph_store.py`, `tests/test_code_graph_store_integration.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sqlite_graph_store.py`:

```python
def test_sqlite_list_symbol_index_returns_identity_and_diff_fields_only(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "h1"},
        {"id": "b", "user_id": "u1", "repo_id": "r2", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "h2"},
    ])

    rows = sqlite_store.list_symbol_index("u1", "r1")

    assert rows == [
        {"id": "a", "name": "foo", "kind": "function", "file_path": "a.py", "content_hash": "h1"}
    ]


def test_sqlite_list_symbol_index_excludes_text_entities(sqlite_store):
    # get_subgraph mixes in mentioning TextEntity nodes (no file_path); the reindex
    # path had to filter those out by hand. list_symbol_index never returns them.
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "h1"},
    ])
    sqlite_store.upsert_text_entities([
        {"id": "te1", "user_id": "u1", "name": "Thing", "entity_type": "concept",
         "source_doc_id": "d1", "source_memory_id": None},
    ])
    sqlite_store.upsert_mentions_edges([{"source": "te1", "target": "a"}])

    rows = sqlite_store.list_symbol_index("u1", "r1")

    assert [r["id"] for r in rows] == ["a"]
```

Append to `tests/test_local_multi_repo_graph_store.py`:

```python
def test_local_multi_repo_list_symbol_index_reads_the_per_repo_store(tmp_path):
    from app.graph.code_graph_store import LocalMultiRepoGraphStore

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    store = LocalMultiRepoGraphStore(str(tmp_path / "central" / "graph.sqlite"))
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": str(repo_dir),
         "local_path": str(repo_dir), "last_indexed_at": "now"},
        [{"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
          "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
          "content_hash": "h1"}],
        [],
    )

    rows = store.list_symbol_index("u1", "r1")

    assert rows == [
        {"id": "a", "name": "foo", "kind": "function", "file_path": "a.py", "content_hash": "h1"}
    ]


def test_local_multi_repo_list_symbol_index_empty_for_unknown_repo(tmp_path):
    from app.graph.code_graph_store import LocalMultiRepoGraphStore

    store = LocalMultiRepoGraphStore(str(tmp_path / "central" / "graph.sqlite"))

    assert store.list_symbol_index("u1", "nope") == []
```

Append to `tests/test_code_graph_store_integration.py`. That file already has a module-level `pytestmark = pytest.mark.skipif(not NEO4J_TEST_URL, ...)`, so no per-test decorator is needed, and its `neo4j_store` fixture only cleans up nodes whose `user_id` starts with `test-` — every `user_id` in a new test **must** use that prefix or it will leak rows between runs:

```python
def test_neo4j_list_symbol_index_returns_identity_and_diff_fields(neo4j_store):
    neo4j_store.replace_repo_graph(
        {"user_id": "test-u-idx", "repo_id": "test-r-idx", "source": "s",
         "local_path": "/tmp/test-r-idx", "last_indexed_at": "now"},
        [{"id": "int-x", "user_id": "test-u-idx", "repo_id": "test-r-idx", "kind": "function",
          "name": "foo", "file_path": "a.py", "start_line": 1, "end_line": 2,
          "language": "python", "content_hash": "h1"}],
        [],
    )

    rows = neo4j_store.list_symbol_index("test-u-idx", "test-r-idx")

    assert rows == [
        {"id": "int-x", "name": "foo", "kind": "function", "file_path": "a.py", "content_hash": "h1"}
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sqlite_graph_store.py tests/test_local_multi_repo_graph_store.py -k list_symbol_index -v`

Expected: FAIL with `AttributeError: 'SqliteGraphStore' object has no attribute 'list_symbol_index'`

- [ ] **Step 3: Declare the abstract method**

In `app/graph/code_graph_store.py`, in the `GraphStore` ABC, directly after the `get_subgraph` declaration (line 63-64), add:

```python
    @abstractmethod
    def list_symbol_index(self, user_id: str, repo_id: str) -> list[dict]:
        """One dict per CodeSymbol in the repo with exactly id/name/kind/file_path/
        content_hash -- the identity and change-detection fields, nothing else.
        Incremental reindex needs only these, so it shouldn't pay for get_subgraph's
        edges, mentioning text entities, and full symbol rows."""
        ...
```

- [ ] **Step 4: Implement on `Neo4jGraphStore`**

In `Neo4jGraphStore`, directly after `get_subgraph` (which ends at line 301), add:

```python
    def list_symbol_index(self, user_id: str, repo_id: str) -> list[dict]:
        records, _, _ = self._driver.execute_query(
            """
            MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
            RETURN n.id AS id, n.name AS name, n.kind AS kind,
                   n.file_path AS file_path, n.content_hash AS content_hash
            """,
            user_id=user_id, repo_id=repo_id,
        )
        return [dict(record) for record in records]
```

- [ ] **Step 5: Implement on `SqliteGraphStore`**

In `SqliteGraphStore`, directly after `get_subgraph`, add:

```python
    def list_symbol_index(self, user_id: str, repo_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, kind, file_path, content_hash FROM code_symbols "
                "WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_symbol_ids(self, user_id: str, repo_id: str) -> list[str]:
        """Just the symbol ids for one repo. LocalMultiRepoGraphStore needs these to
        ask the central store about MENTIONS edges without pulling whole rows."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM code_symbols WHERE user_id = ? AND repo_id = ?", (user_id, repo_id)
            ).fetchall()
        return [row["id"] for row in rows]
```

(`list_symbol_ids` is used by Task 5. It lives here now so both land in one coherent SQLite change.)

- [ ] **Step 6: Implement on `LocalMultiRepoGraphStore`**

In `LocalMultiRepoGraphStore`, directly after `get_subgraph` (ends at line 790), add:

```python
    def list_symbol_index(self, user_id: str, repo_id: str) -> list[dict]:
        # Symbols live only in the per-repo store; the central store has no code_symbols
        # rows for this repo, so there's nothing to merge in here.
        repo = self._central.get_repo(user_id, repo_id)
        if repo is None:
            return []
        return self._repo_store(repo["local_path"]).list_symbol_index(user_id, repo_id)
```

- [ ] **Step 7: Implement on `FakeGraphStore`**

In `tests/conftest.py`, directly after `get_subgraph` (ends at line 89), add:

```python
    def list_symbol_index(self, user_id: str, repo_id: str) -> list[dict]:
        return [
            {"id": s["id"], "name": s["name"], "kind": s["kind"],
             "file_path": s["file_path"], "content_hash": s.get("content_hash")}
            for s in self.symbols.values()
            if s["user_id"] == user_id and s["repo_id"] == repo_id
        ]
```

- [ ] **Step 8: Run the tests**

Run: `pytest tests/test_sqlite_graph_store.py tests/test_local_multi_repo_graph_store.py tests/test_code_graph_store_integration.py -v`

Expected: PASS. The Neo4j integration tests report `SKIPPED` unless `NEO4J_TEST_URL` is set — that is the correct outcome without a live Neo4j.

- [ ] **Step 9: Run the whole suite to catch any store that missed the new abstract method**

Run: `pytest -q`

Expected: PASS. A failure here reading `TypeError: Can't instantiate abstract class X with abstract method list_symbol_index` means an implementation was missed — go back and add it.

- [ ] **Step 10: Commit**

```bash
git add app/graph/code_graph_store.py tests/conftest.py tests/test_sqlite_graph_store.py tests/test_local_multi_repo_graph_store.py tests/test_code_graph_store_integration.py
git commit -m "feat(graph): add GraphStore.list_symbol_index

Narrow per-repo read returning only id/name/kind/file_path/content_hash --
the fields incremental reindex diffs on. Implemented on all four stores.
Wired into repo_watcher next."
```

---

### Task 4: Incremental reindex reads the narrow index, not the whole subgraph

`reindex_paths` calls `get_subgraph` on every debounced save, pulling every symbol row, every code edge (discarded), every mentions edge, and joined text entities — to diff one changed file.

**Files:**
- Modify: `app/graph/repo_watcher.py:213-216`
- Test: `tests/test_repo_watcher.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_repo_watcher.py`:

```python
def test_reindex_paths_does_not_load_the_full_subgraph(tmp_path, graph_store, qdrant):
    # get_subgraph pulls every symbol row, every edge, and joined text entities.
    # Diffing one changed file only needs the narrow symbol index.
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")

    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("u1", "r1", str(tmp_path))

    calls = {"get_subgraph": 0, "list_symbol_index": 0}
    real_get_subgraph = graph_store.get_subgraph
    real_list_symbol_index = graph_store.list_symbol_index

    def counting_get_subgraph(user_id, repo_id):
        calls["get_subgraph"] += 1
        return real_get_subgraph(user_id, repo_id)

    def counting_list_symbol_index(user_id, repo_id):
        calls["list_symbol_index"] += 1
        return real_list_symbol_index(user_id, repo_id)

    graph_store.get_subgraph = counting_get_subgraph
    graph_store.list_symbol_index = counting_list_symbol_index

    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    manager.reindex_paths("u1", "r1", str(tmp_path), {str(tmp_path / "a.py")}, set())

    assert calls["get_subgraph"] == 0
    assert calls["list_symbol_index"] == 1
```

`_FakeEmbedder` is already defined at the top of `tests/test_repo_watcher.py` (it returns `[[float(len(t))] * 384 for t in texts]`) — reuse it, do not redefine it.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repo_watcher.py::test_reindex_paths_does_not_load_the_full_subgraph -v`

Expected: FAIL with `assert 1 == 0` on the `get_subgraph` count

- [ ] **Step 3: Switch `reindex_paths` to the narrow read**

In `app/graph/repo_watcher.py`, find lines 213-216:

```python
            baseline_nodes, _ = self._graph_store.get_subgraph(user_id, repo_id)
            # get_subgraph also returns mentioning TextEntity nodes, which have no
            # file_path -- only CodeSymbol-shaped nodes matter for the index/diff below.
            code_baseline_nodes = [n for n in baseline_nodes if "file_path" in n]
```

Replace with:

```python
            # Narrow read: get_subgraph would pull every symbol row, every edge (all
            # discarded here), and joined text entities, just to diff one changed file.
            # list_symbol_index returns exactly the id/name/kind/file_path/content_hash
            # the diff and resolve_edges below need, and never includes TextEntity nodes.
            code_baseline_nodes = self._graph_store.list_symbol_index(user_id, repo_id)
```

Nothing else in the method changes: `kept_baseline`, `stale_baseline_by_id`, `index_symbols`, and the `content_hash` comparison in `_update_symbol_embeddings` all read only fields `list_symbol_index` returns.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_repo_watcher.py::test_reindex_paths_does_not_load_the_full_subgraph -v`

Expected: PASS

- [ ] **Step 5: Run the whole watcher suite as a behavioral regression guard**

Run: `pytest tests/test_repo_watcher.py -v`

Expected: PASS — in particular `test_reindex_paths_only_reembeds_the_changed_file`,
`test_reindex_paths_removes_symbols_and_vectors_for_a_deleted_file`,
`test_reindex_paths_resolves_a_call_from_a_changed_file_into_an_unchanged_files_symbol`, and
`test_reindex_paths_drops_rather_than_reresolves_an_edge_from_an_unchanged_file_after_a_rename`,
which together prove the cross-file edge resolution still works off the narrower baseline.

- [ ] **Step 6: Commit**

```bash
git add app/graph/repo_watcher.py tests/test_repo_watcher.py
git commit -m "perf(graph): reindex_paths reads a narrow symbol index

Every debounced save called get_subgraph, materializing all symbol rows,
all edges (immediately discarded), all mentions edges and joined text
entities -- to diff one file. list_symbol_index returns just the fields
the diff and edge resolution use."
```

---

### Task 5: Add `count_subgraph` to the GraphStore interface

**Files:**
- Modify: `app/graph/code_graph_store.py` (ABC, `Neo4jGraphStore`, `SqliteGraphStore`, `LocalMultiRepoGraphStore`)
- Modify: `tests/conftest.py` (`FakeGraphStore`)
- Test: `tests/test_sqlite_graph_store.py`, `tests/test_local_multi_repo_graph_store.py`, `tests/test_code_graph_store_integration.py`

**Critical correctness note.** Each backend's `count_subgraph` must equal `len(x)` of its **own** `get_subgraph` result, and the two backends differ:

- **SQLite** appends a MENTIONS edge for every `mentions_edges` row targeting a repo symbol, *whether or not* a `text_entities` row exists for its source; it adds a node only when the `text_entities` row exists. So: edge count = unjoined `COUNT(*)`, node count adds `COUNT(DISTINCT te.id)` over a join.
- **Neo4j** only produces an edge when a real `TextEntity` node matched, so both counts come from the same matched pattern.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sqlite_graph_store.py`:

```python
def test_sqlite_count_subgraph_matches_get_subgraph_lengths(sqlite_store):
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "h1"},
        {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "h2"},
    ])
    sqlite_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])
    sqlite_store.upsert_text_entities([
        {"id": "te1", "user_id": "u1", "name": "Thing", "entity_type": "concept",
         "source_doc_id": "d1", "source_memory_id": None},
    ])
    sqlite_store.upsert_mentions_edges([{"source": "te1", "target": "a"}])

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")

    assert sqlite_store.count_subgraph("u1", "r1") == (len(nodes), len(edges))


def test_sqlite_count_subgraph_matches_get_subgraph_for_orphan_mentions_edge(sqlite_store):
    # get_subgraph emits a MENTIONS edge even when no text_entities row backs its
    # source, but no node -- the counts have to reproduce that asymmetry exactly.
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "h1"},
    ])
    sqlite_store.upsert_mentions_edges([{"source": "ghost", "target": "a"}])

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")

    assert (len(nodes), len(edges)) == (1, 1)
    assert sqlite_store.count_subgraph("u1", "r1") == (1, 1)


def test_sqlite_count_subgraph_empty_repo(sqlite_store):
    assert sqlite_store.count_subgraph("u1", "nope") == (0, 0)
```

Append to `tests/test_local_multi_repo_graph_store.py`:

```python
def test_local_multi_repo_count_subgraph_matches_get_subgraph_lengths(tmp_path):
    from app.graph.code_graph_store import LocalMultiRepoGraphStore

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    store = LocalMultiRepoGraphStore(str(tmp_path / "central" / "graph.sqlite"))
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": str(repo_dir),
         "local_path": str(repo_dir), "last_indexed_at": "now"},
        [{"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
          "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
          "content_hash": "h1"},
         {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "bar",
          "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python",
          "content_hash": "h2"}],
        [{"source": "a", "target": "b", "type": "CALLS"}],
    )
    store.upsert_text_entities([
        {"id": "te1", "user_id": "u1", "name": "Thing", "entity_type": "concept",
         "source_doc_id": "d1", "source_memory_id": None},
    ])
    store.upsert_mentions_edges([{"source": "te1", "target": "a"}])

    nodes, edges = store.get_subgraph("u1", "r1")

    assert store.count_subgraph("u1", "r1") == (len(nodes), len(edges))


def test_local_multi_repo_count_subgraph_zero_for_unknown_repo(tmp_path):
    from app.graph.code_graph_store import LocalMultiRepoGraphStore

    store = LocalMultiRepoGraphStore(str(tmp_path / "central" / "graph.sqlite"))

    assert store.count_subgraph("u1", "nope") == (0, 0)
```

Append to `tests/test_code_graph_store_integration.py`. Same rules as Task 3: the module-level `pytestmark` handles gating, and `user_id` must start with `test-` for the fixture's cleanup to reach it.

```python
def test_neo4j_count_subgraph_matches_get_subgraph_lengths(neo4j_store):
    neo4j_store.replace_repo_graph(
        {"user_id": "test-u-cnt", "repo_id": "test-r-cnt", "source": "s",
         "local_path": "/tmp/test-r-cnt", "last_indexed_at": "now"},
        [{"id": "int-c1", "user_id": "test-u-cnt", "repo_id": "test-r-cnt", "kind": "function",
          "name": "foo", "file_path": "a.py", "start_line": 1, "end_line": 2,
          "language": "python", "content_hash": "h1"},
         {"id": "int-c2", "user_id": "test-u-cnt", "repo_id": "test-r-cnt", "kind": "function",
          "name": "bar", "file_path": "b.py", "start_line": 1, "end_line": 2,
          "language": "python", "content_hash": "h2"}],
        [{"source": "int-c1", "target": "int-c2", "type": "CALLS"}],
    )

    nodes, edges = neo4j_store.get_subgraph("test-u-cnt", "test-r-cnt")

    assert neo4j_store.count_subgraph("test-u-cnt", "test-r-cnt") == (len(nodes), len(edges))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sqlite_graph_store.py tests/test_local_multi_repo_graph_store.py -k count_subgraph -v`

Expected: FAIL with `AttributeError: 'SqliteGraphStore' object has no attribute 'count_subgraph'`

- [ ] **Step 3: Declare the abstract method**

In the `GraphStore` ABC, directly after the `list_symbol_index` declaration added in Task 3, add:

```python
    @abstractmethod
    def count_subgraph(self, user_id: str, repo_id: str) -> tuple[int, int]:
        """(node_count, edge_count) equal to len() of this same backend's get_subgraph
        result, computed with aggregates instead of materializing the graph. Backends
        differ on whether an unbacked MENTIONS edge yields an edge — each implementation
        must match its own get_subgraph, not the other's."""
        ...
```

- [ ] **Step 4: Implement on `Neo4jGraphStore`**

Directly after `list_symbol_index` in `Neo4jGraphStore`, add:

```python
    def count_subgraph(self, user_id: str, repo_id: str) -> tuple[int, int]:
        # Three aggregate queries mirroring get_subgraph's two match patterns -- counts
        # come back as scalars, no node or relationship rows cross the wire.
        symbol_records, _, _ = self._driver.execute_query(
            "MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id}) RETURN count(n) AS c",
            user_id=user_id, repo_id=repo_id,
        )
        edge_records, _, _ = self._driver.execute_query(
            """
            MATCH (:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
                  -[r]->(:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
            RETURN count(r) AS c
            """,
            user_id=user_id, repo_id=repo_id,
        )
        mention_records, _, _ = self._driver.execute_query(
            """
            MATCH (te:TextEntity {user_id: $user_id})
                  -[m:MENTIONS]->(:CodeSymbol {user_id: $user_id, repo_id: $repo_id})
            RETURN count(m) AS edges, count(DISTINCT te) AS entities
            """,
            user_id=user_id, repo_id=repo_id,
        )
        node_count = symbol_records[0]["c"] + mention_records[0]["entities"]
        edge_count = edge_records[0]["c"] + mention_records[0]["edges"]
        return node_count, edge_count
```

- [ ] **Step 5: Implement on `SqliteGraphStore`**

Directly after `list_symbol_ids` in `SqliteGraphStore`, add:

```python
    def count_subgraph(self, user_id: str, repo_id: str) -> tuple[int, int]:
        # Mirrors this class's get_subgraph exactly, including its asymmetry: a
        # mentions_edges row whose source has no text_entities row still contributes
        # an edge but not a node -- hence the unjoined edge count and the joined
        # distinct-entity count.
        scope = "(SELECT id FROM code_symbols WHERE user_id = ? AND repo_id = ?)"
        with self._lock:
            symbol_count = self._conn.execute(
                "SELECT COUNT(*) FROM code_symbols WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchone()[0]
            if not symbol_count:
                return 0, 0
            code_edge_count = self._conn.execute(
                f"SELECT COUNT(*) FROM code_edges WHERE source IN {scope} AND target IN {scope}",
                (user_id, repo_id, user_id, repo_id),
            ).fetchone()[0]
            mention_edge_count = self._conn.execute(
                f"SELECT COUNT(*) FROM mentions_edges WHERE target IN {scope}", (user_id, repo_id)
            ).fetchone()[0]
            entity_count = self._conn.execute(
                f"SELECT COUNT(DISTINCT te.id) FROM mentions_edges m "
                f"JOIN text_entities te ON te.id = m.source WHERE m.target IN {scope}",
                (user_id, repo_id),
            ).fetchone()[0]
        return symbol_count + entity_count, code_edge_count + mention_edge_count

    def count_mentioning_text_entities(self, symbol_ids: list[str]) -> tuple[int, int]:
        """(distinct text-entity count, MENTIONS edge count) targeting any of `symbol_ids`.
        Counting counterpart to get_mentioning_text_entities, for LocalMultiRepoGraphStore's
        count_subgraph -- same chunked IN-list, no row materialization. Matches
        get_mentioning_text_entities' asymmetry: every mentions_edges row is an edge,
        only entity-backed sources are nodes."""
        if not symbol_ids:
            return 0, 0
        entity_ids: set[str] = set()
        edge_count = 0
        with self._lock:
            for chunk in _chunked(symbol_ids):
                placeholders = _in_clause(len(chunk))
                edge_count += self._conn.execute(
                    f"SELECT COUNT(*) FROM mentions_edges WHERE target IN {placeholders}", chunk
                ).fetchone()[0]
                rows = self._conn.execute(
                    f"SELECT DISTINCT m.source AS source FROM mentions_edges m "
                    f"JOIN text_entities te ON te.id = m.source WHERE m.target IN {placeholders}",
                    chunk,
                ).fetchall()
                entity_ids.update(row["source"] for row in rows)
        return len(entity_ids), edge_count
```

- [ ] **Step 6: Implement on `LocalMultiRepoGraphStore`**

Directly after `list_symbol_index` in `LocalMultiRepoGraphStore`, add:

```python
    def count_subgraph(self, user_id: str, repo_id: str) -> tuple[int, int]:
        # Symbols and code edges are counted in the per-repo store; text entities and
        # MENTIONS edges live in the central store and can only be matched by symbol id,
        # so the ids do have to cross (list_symbol_ids keeps that to one column, and
        # count_mentioning_text_entities chunks the IN-list).
        repo = self._central.get_repo(user_id, repo_id)
        if repo is None:
            return 0, 0
        repo_store = self._repo_store(repo["local_path"])
        symbol_count, code_edge_count = repo_store.count_subgraph(user_id, repo_id)
        symbol_ids = repo_store.list_symbol_ids(user_id, repo_id)
        entity_count, mention_edge_count = self._central.count_mentioning_text_entities(symbol_ids)
        return symbol_count + entity_count, code_edge_count + mention_edge_count
```

- [ ] **Step 7: Implement on `FakeGraphStore`**

In `tests/conftest.py`, directly after `list_symbol_index`, add:

```python
    def count_subgraph(self, user_id: str, repo_id: str) -> tuple[int, int]:
        nodes, edges = self.get_subgraph(user_id, repo_id)
        return len(nodes), len(edges)
```

The fake is in-memory, so delegating is correct and keeps it trivially consistent with `get_subgraph`.

- [ ] **Step 8: Run the tests**

Run: `pytest tests/test_sqlite_graph_store.py tests/test_local_multi_repo_graph_store.py tests/test_code_graph_store_integration.py -v`

Expected: PASS (Neo4j tests SKIPPED without `NEO4J_TEST_URL`)

- [ ] **Step 9: Run the whole suite**

Run: `pytest -q`

Expected: PASS. `TypeError: Can't instantiate abstract class ... count_subgraph` means an implementation was missed.

- [ ] **Step 10: Commit**

```bash
git add app/graph/code_graph_store.py tests/conftest.py tests/test_sqlite_graph_store.py tests/test_local_multi_repo_graph_store.py tests/test_code_graph_store_integration.py
git commit -m "feat(graph): add GraphStore.count_subgraph

Aggregate node/edge counts for one repo without materializing the graph.
Each backend matches its own get_subgraph semantics, including SQLite's
quirk that an unbacked mentions_edges row yields an edge but no node.
Wired into the dashboard next."
```

---

### Task 6: Dashboard counts repos instead of materializing every graph

`project_breakdown` calls `get_subgraph` per repo — every node and edge of every ingested repo across all users, transferred into Python, for two integers.

**Files:**
- Modify: `app/dashboard/queries.py:47-59`
- Test: `tests/test_dashboard_queries.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_queries.py`:

```python
def test_project_breakdown_does_not_materialize_the_graph(graph_store):
    graph_store.upsert_repo({
        "user_id": "u1", "repo_id": "r1", "source": "s",
        "local_path": "/tmp/r1", "last_indexed_at": "now",
    })
    graph_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    graph_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    calls = {"get_subgraph": 0}
    real_get_subgraph = graph_store.get_subgraph

    def counting_get_subgraph(user_id, repo_id):
        calls["get_subgraph"] += 1
        return real_get_subgraph(user_id, repo_id)

    graph_store.get_subgraph = counting_get_subgraph
    # FakeGraphStore.count_subgraph delegates to self.get_subgraph, which would pick
    # up the instance attribute set on the line above and make the counter fire even
    # on the fixed code. Stub it so the assertion measures project_breakdown only.
    graph_store.count_subgraph = lambda user_id, repo_id: (2, 1)

    result = queries.project_breakdown(graph_store)

    assert result == [
        {"repo_id": "r1", "node_count": 2, "edge_count": 1, "last_indexed_at": "now"}
    ]
    assert calls["get_subgraph"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_queries.py::test_project_breakdown_does_not_materialize_the_graph -v`

Expected: FAIL with `assert 1 == 0` on the `get_subgraph` count

- [ ] **Step 3: Switch `project_breakdown` to counts**

In `app/dashboard/queries.py`, replace `project_breakdown` (lines 47-59) with:

```python
def project_breakdown(graph_store) -> list[dict]:
    if graph_store is None:
        return []
    result = []
    for repo in graph_store.list_repos():
        # count_subgraph, not get_subgraph: this endpoint only needs two integers, and
        # get_subgraph would pull every node and edge of every repo of every user into
        # Python to compute them.
        node_count, edge_count = graph_store.count_subgraph(repo["user_id"], repo["repo_id"])
        result.append({
            "repo_id": repo["repo_id"],
            "node_count": node_count,
            "edge_count": edge_count,
            "last_indexed_at": repo.get("last_indexed_at"),
        })
    return result
```

- [ ] **Step 4: Run the dashboard query tests**

Run: `pytest tests/test_dashboard_queries.py -v`

Expected: PASS, including the pre-existing `test_project_breakdown_reports_node_and_edge_counts`, `test_project_breakdown_empty_when_no_repos`, and `test_project_breakdown_empty_when_graph_store_is_none`.

- [ ] **Step 5: Commit**

```bash
git add app/dashboard/queries.py tests/test_dashboard_queries.py
git commit -m "perf(dashboard): count repo graphs instead of materializing them

project_breakdown called get_subgraph per repo, transferring every node
and edge of every ingested repo into Python to compute two integers."
```

---

### Task 7: `fuse_graph_context` builds its graph once

`bfs_query` starts with a full `MultiDiGraph` construction. `fuse_graph_context` calls it once per keyword (up to 3), so the entire repo subgraph is rebuilt three times per RAG query — and each call also computes `_edges_within` over every edge, which `fuse_graph_context` then discards.

**Files:**
- Modify: `app/graph/graph_query.py:32-55` (`bfs_query`), `:95-121` (`fuse_graph_context`)
- Test: `tests/test_graph_query.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_graph_query.py`:

```python
def test_fuse_graph_context_builds_the_graph_once_across_keywords(monkeypatch, graph_store):
    from app.graph import graph_query

    graph_store.upsert_repo({
        "user_id": "u1", "repo_id": "r1", "source": "s",
        "local_path": "/tmp/r1", "last_indexed_at": "now",
    })
    graph_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "retrieve_chunks",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "class", "name": "Embedder",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python"},
        {"id": "c", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "rerank",
         "file_path": "c.py", "start_line": 1, "end_line": 2, "language": "python"},
    ])
    graph_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    builds = {"count": 0}
    real_build_graph = graph_query._build_graph

    def counting_build_graph(nodes, edges):
        builds["count"] += 1
        return real_build_graph(nodes, edges)

    monkeypatch.setattr(graph_query, "_build_graph", counting_build_graph)

    nodes, _edges = graph_query.fuse_graph_context(
        graph_store, "u1", "r1", ["retrieve_chunks", "Embedder", "rerank"]
    )

    assert {n["name"] for n in nodes} == {"retrieve_chunks", "Embedder", "rerank"}
    assert builds["count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_graph_query.py::test_fuse_graph_context_builds_the_graph_once_across_keywords -v`

Expected: FAIL with `assert 3 == 1`

- [ ] **Step 3: Split the BFS core out of `bfs_query`**

In `app/graph/graph_query.py`, replace `bfs_query` (lines 32-55) with:

```python
def _bfs_from_graph(g: nx.MultiDiGraph, keyword: str, depth: int) -> set[str]:
    """BFS core operating on an already-built graph, so callers running several
    keywords over the same graph pay the build cost once."""
    keyword_lower = keyword.lower()
    seeds = {n for n, data in g.nodes(data=True) if keyword_lower in data.get("name", "").lower()}
    if not seeds:
        return set()

    visited = set(seeds)
    frontier = set(seeds)
    for _ in range(depth):
        next_frontier: set[str] = set()
        for node_id in frontier:
            next_frontier.update(g.successors(node_id))
            next_frontier.update(g.predecessors(node_id))
        next_frontier -= visited
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier
    return visited


def bfs_query(nodes: list[dict], edges: list[dict], keyword: str, depth: int = 2) -> tuple[list[dict], list[dict]]:
    """Seed on nodes whose name contains `keyword` (case-insensitive), then
    expand outward (both directions) up to `depth` hops. Returns the matched
    subgraph as (nodes, edges); ([], []) if nothing matches."""
    g = _build_graph(nodes, edges)
    visited = _bfs_from_graph(g, keyword, depth)
    if not visited:
        return [], []
    return [g.nodes[n] for n in visited], _edges_within(g, visited)
```

- [ ] **Step 4: Rewrite `fuse_graph_context` to build once**

Replace `fuse_graph_context` (lines 95-121) with:

```python
def fuse_graph_context(
    graph_store, user_id: str, repo_id: str, keywords: list[str], max_nodes: int = 15
) -> tuple[list[dict], list[dict]]:
    """Depth-1 BFS per keyword against the repo's subgraph, merged and deduped
    by node id, capped at `max_nodes` (earlier keywords kept preferentially).
    Empty `keywords` (nothing relevant found upstream) short-circuits to
    ([], []) without touching the graph store."""
    if not keywords:
        return [], []

    nodes, edges = graph_store.get_subgraph(user_id, repo_id)
    # Built once and reused across keywords: calling bfs_query per keyword rebuilt
    # the whole repo graph each time, and computed a full edge scan per call that
    # this function discarded in favour of the merged_edges pass below.
    g = _build_graph(nodes, edges)

    seen_ids: set[str] = set()
    merged_nodes: list[dict] = []
    for kw in keywords:
        for node_id in _bfs_from_graph(g, kw, depth=1):
            if node_id not in seen_ids:
                seen_ids.add(node_id)
                merged_nodes.append(g.nodes[node_id])
        if len(merged_nodes) >= max_nodes:
            break

    merged_nodes = merged_nodes[:max_nodes]
    kept_ids = {n["id"] for n in merged_nodes}
    merged_edges = [e for e in edges if e["source"] in kept_ids and e["target"] in kept_ids]
    return merged_nodes, merged_edges
```

Behavior is unchanged, including the pre-existing property that node order within one keyword's result comes from set iteration (so which nodes survive `[:max_nodes]` at the boundary was already unordered). Do not "fix" that here — it is out of scope.

- [ ] **Step 5: Run the graph query tests**

Run: `pytest tests/test_graph_query.py -v`

Expected: PASS, including every pre-existing `bfs_query` / `shortest_path` / `explain_node` / `fuse_graph_context` test — those are the guard that the refactor preserved semantics.

- [ ] **Step 6: Run the RAG fusion tests that consume this**

Run: `pytest tests/test_graph_fusion.py tests/test_mcp_graph_tools.py tests/test_context.py -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/graph/graph_query.py tests/test_graph_query.py
git commit -m "perf(graph): build the fusion graph once, not once per keyword

fuse_graph_context called bfs_query per keyword and bfs_query starts by
building a full MultiDiGraph, so a 3-keyword RAG query rebuilt the entire
repo subgraph three times -- and each call also ran a full edge scan whose
result fuse_graph_context threw away."
```

---

### Task 8: Paginate the dashboard's per-user Qdrant scroll

`_count_by_user_id` scrolls with `limit=10000` and drops the returned cursor, so beyond 10000 points the numbers are silently wrong — a correctness bug, not just a slow path.

**Files:**
- Modify: `app/dashboard/queries.py:76-86`
- Test: `tests/test_dashboard_queries.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_queries.py`:

```python
def test_count_by_user_id_follows_the_scroll_cursor(usage_store):
    # The old implementation passed limit=10000 and dropped the returned cursor,
    # so anything past the first page was silently uncounted.
    class _PagedClient:
        def __init__(self):
            self.pages = [
                ([_Point("u1"), _Point("u1")], "cursor-1"),
                ([_Point("u2")], None),
            ]
            self.offsets_seen = []

        def scroll(self, collection_name, limit, with_payload, offset=None):
            self.offsets_seen.append(offset)
            return self.pages.pop(0)

    client = _PagedClient()

    counts = queries._count_by_user_id(client, "rag_documents")

    assert counts == {"u1": 2, "u2": 1}
    assert client.offsets_seen == [None, "cursor-1"]
```

And add this tiny helper near the top of `tests/test_dashboard_queries.py`, after the imports:

```python
class _Point:
    def __init__(self, user_id):
        self.payload = {"user_id": user_id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_queries.py::test_count_by_user_id_follows_the_scroll_cursor -v`

Expected: FAIL with `TypeError: scroll() got an unexpected keyword argument 'offset'` — the current implementation never passes one.

- [ ] **Step 3: Paginate**

In `app/dashboard/queries.py`, replace `_count_by_user_id` (lines 76-86) with:

```python
def _count_by_user_id(client, collection: str) -> dict[str, int]:
    # Follows the scroll cursor to the end. The previous single limit=10000 call
    # dropped the returned offset, so a collection larger than one page produced
    # silently wrong counts rather than slow ones.
    counts: dict[str, int] = {}
    offset = None
    try:
        while True:
            points, offset = client.scroll(
                collection_name=collection, limit=1000, with_payload=["user_id"], offset=offset
            )
            for p in points:
                uid = p.payload.get("user_id")
                if uid:
                    counts[uid] = counts.get(uid, 0) + 1
            if offset is None:
                return counts
    except Exception:
        return counts
```

The `except` returns whatever was counted so far, matching the old behavior of returning an empty dict when the very first call failed.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_dashboard_queries.py -v`

Expected: PASS, including the pre-existing `test_user_breakdown_combines_usage_and_qdrant_counts` which exercises this against the real in-memory Qdrant.

- [ ] **Step 5: Commit**

```bash
git add app/dashboard/queries.py tests/test_dashboard_queries.py
git commit -m "fix(dashboard): follow the scroll cursor when counting by user

_count_by_user_id passed limit=10000 and discarded the returned offset, so
past one page the per-user document and memory counts were silently wrong."
```

---

### Task 9: Raise the LLM idle-unload default

Five minutes idle drops the reasoning model; the next request then pays a full multi-GB `pipeline(...)` load while holding the generation lock, so every concurrent caller waits on that cold start. The MCP usage pattern is bursty-but-interactive — exactly the worst case.

**Files:**
- Modify: `app/config.py:12`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_settings_reasoning_model_idle_unload_default(monkeypatch):
    monkeypatch.delenv("REASONING_MODEL_IDLE_UNLOAD_SECONDS", raising=False)

    settings = Settings()

    # 300s made a bursty-but-interactive MCP session pay a full model reload
    # (under the generation lock) between question batches.
    assert settings.reasoning_model_idle_unload_seconds == 1800
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_settings_reasoning_model_idle_unload_default -v`

Expected: FAIL with `assert 300 == 1800`

- [ ] **Step 3: Change the default**

In `app/config.py`, replace line 12:

```python
    reasoning_model_idle_unload_seconds: int = 300  # auto-unload reasoning model after this long unused
```

with:

```python
    # Auto-unload the reasoning model after this long unused. Reloading costs a full
    # multi-GB pipeline() init while holding ReasoningLLM's generation lock, so this
    # wants to be longer than a typical gap between question batches in one session.
    reasoning_model_idle_unload_seconds: int = 1800
```

- [ ] **Step 4: Run the config and llm tests**

Run: `pytest tests/test_config.py tests/test_llm.py -v`

Expected: PASS. If a test in `tests/test_config.py` (e.g. `test_settings_defaults`) asserts the old 300, update that assertion to 1800 in the same commit — do not add a second competing assertion.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "perf(llm): raise idle-unload default from 300s to 1800s

Reloading the reasoning model costs a full pipeline() init under the
generation lock, so a 5-minute idle window made every gap between question
batches a cold start that all concurrent callers blocked on."
```

---

### Task 10: Dashboard auth fails closed again

Currently `_require_auth` returns (allows) when `DASHBOARD_USER`/`DASHBOARD_PASSWORD` are blank. Both default to empty and `docker/.env.example` ships them blank, so a default `install.sh` run serves `/dashboard` and `/api/dashboard/summary` unauthenticated on `0.0.0.0:8030`.

**Files:**
- Modify: `app/dashboard/router.py:1-5` (module docstring), `:21-33` (`_require_auth`)
- Modify: `tests/test_dashboard_api.py:46-63`
- Modify: `CLAUDE.md`, `README.md`

- [ ] **Step 1: Rewrite the failing-open test as a failing-closed test**

In `tests/test_dashboard_api.py`, replace the whole of `test_dashboard_serves_unauthenticated_when_auth_env_unset` (lines 46-63) with:

```python
def test_dashboard_returns_503_when_auth_env_unset(qdrant, graph_store, usage_store, monkeypatch):
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()

    from app.dashboard.router import build_dashboard_router

    app = FastAPI()
    router = build_dashboard_router(
        get_client=lambda: qdrant, get_graph_store=lambda: graph_store,
        get_usage_store=lambda: usage_store, get_embedder=lambda: _Embedder(),
    )
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/dashboard")
    assert resp.status_code == 503

    # The JSON summary is the sensitive one -- per-user ids and counts, every repo
    # across every user, and backend error text. It must be closed too.
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 503


def test_dashboard_returns_503_when_only_user_is_set(qdrant, graph_store, usage_store, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    from app.config import get_settings
    get_settings.cache_clear()

    from app.dashboard.router import build_dashboard_router

    app = FastAPI()
    router = build_dashboard_router(
        get_client=lambda: qdrant, get_graph_store=lambda: graph_store,
        get_usage_store=lambda: usage_store, get_embedder=lambda: _Embedder(),
    )
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/dashboard", auth=("admin", ""))
    assert resp.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_api.py -k 503 -v`

Expected: 2 FAILs, `assert 200 == 503`

- [ ] **Step 3: Make `_require_auth` fail closed**

In `app/dashboard/router.py`, replace `_require_auth` (lines 21-33) with:

```python
def _require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    settings = get_settings()
    dashboard_password = settings.dashboard_password.get_secret_value()
    if not settings.dashboard_user or not dashboard_password:
        # Fail CLOSED. Both settings default to empty and docker/.env.example ships
        # them blank, so failing open meant a default install served usage stats,
        # per-user breakdowns, every repo across every user, and backend error text
        # to anything that could reach the port.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="dashboard auth not configured: set DASHBOARD_USER and DASHBOARD_PASSWORD",
        )
    if credentials is None or not (
        secrets.compare_digest(credentials.username, settings.dashboard_user)
        and secrets.compare_digest(credentials.password, dashboard_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
```

- [ ] **Step 4: Update the module docstring**

Replace lines 1-5 of `app/dashboard/router.py`:

```python
"""GET /dashboard (HTML page) and GET /api/dashboard/summary (JSON), both
behind HTTP Basic Auth. Fails OPEN: if DASHBOARD_USER/DASHBOARD_PASSWORD are
unset, both routes serve unauthenticated rather than returning 503. Only set
one of the two blank if you're intentionally exposing this on a trusted
network -- both routes return usage stats, health, and per-user breakdown."""
```

with:

```python
"""GET /dashboard (HTML page) and GET /api/dashboard/summary (JSON), both
behind HTTP Basic Auth. Fails CLOSED: if DASHBOARD_USER/DASHBOARD_PASSWORD are
unset, both routes return 503 rather than serving unauthenticated. Both
credentials default to empty and docker/.env.example ships them blank, so
failing open would expose usage stats, health (including backend error text),
every repo across every user, and the per-user breakdown on any default
install -- install.sh binds uvicorn to 0.0.0.0."""
```

- [ ] **Step 5: Run the dashboard API tests**

Run: `pytest tests/test_dashboard_api.py -v`

Expected: PASS. `test_dashboard_requires_auth_header`, `test_dashboard_rejects_wrong_credentials`, and `test_dashboard_accepts_correct_credentials` all set credentials via `_make_client` and are unaffected.

- [ ] **Step 6: Update `CLAUDE.md`**

In `CLAUDE.md`, find this text in the `dashboard/` bullet under **Module layout**:

```
  (`router.py`) is HTTP Basic and **fails open**: if
  `dashboard_user`/`dashboard_password` are unset, dashboard routes serve
  unauthenticated rather than 503 — deliberate, not an oversight.
```

Replace with:

```
  (`router.py`) is HTTP Basic and **fails closed**: if
  `dashboard_user`/`dashboard_password` are unset, dashboard routes return
  503. Both default to empty and `docker/.env.example` ships them blank, so
  a fresh install must set them before the dashboard will serve.
```

- [ ] **Step 7: Update `README.md`**

Find the section of `README.md` that documents the dashboard (search for `DASHBOARD_USER`). Add immediately after the credential variables are introduced:

```markdown
`DASHBOARD_USER` and `DASHBOARD_PASSWORD` are required for the dashboard to
serve. `install.sh` copies `docker/.env.example` with both blank, so `/dashboard`
and `/api/dashboard/summary` return `503 dashboard auth not configured` until you
set them in `.env` and restart. This is deliberate: `install.sh` binds uvicorn to
`0.0.0.0:8030`, and the summary endpoint exposes per-user ids and counts, every
ingested repo across every user, and backend health error text.
```

If no such section exists, add it under the configuration/env-vars section of the README.

- [ ] **Step 8: Commit**

```bash
git add app/dashboard/router.py tests/test_dashboard_api.py CLAUDE.md README.md
git commit -m "fix(auth): dashboard auth fails closed again

_require_auth returned (allowed) when DASHBOARD_USER/DASHBOARD_PASSWORD were
blank. Both default to empty and docker/.env.example ships them blank, and
install.sh binds uvicorn to 0.0.0.0 -- so a default install served the
dashboard and its JSON summary (per-user ids and counts, every repo across
every user, backend error text) to anything that could reach port 8030.
Restores the 503."
```

---

### Task 11: Full verification

**Files:** none modified — this is the gate before the branch is considered done.

- [ ] **Step 1: Run the complete test suite**

Run: `pytest -q`

Expected: all tests PASS, with the Neo4j integration tests reported as SKIPPED (no `NEO4J_TEST_URL` in this environment). Zero failures and zero errors.

- [ ] **Step 2: Verify against a live Neo4j if one is available**

Run: `NEO4J_TEST_URL=bolt://localhost:7687 pytest tests/test_code_graph_store_integration.py -v`

Expected: PASS with the new `list_symbol_index` and `count_subgraph` tests running rather than skipping. If no Neo4j is reachable, record that these two remain unverified against a real driver — do not claim they were verified.

- [ ] **Step 3: Smoke-test local deploy mode end to end**

Run:

```bash
DEPLOY_MODE=local python3 -c "
from app.graph.code_graph_store import get_graph_store
s = get_graph_store()
print('ping', s.ping())
print('repos', len(s.list_repos()))
for r in s.list_repos():
    print(r['repo_id'], s.count_subgraph(r['user_id'], r['repo_id']), len(s.list_symbol_index(r['user_id'], r['repo_id'])))
"
```

Expected: `ping True`, then one line per already-ingested repo showing a `(node_count, edge_count)` tuple and a symbol-index length, with no exception. If there are no ingested repos yet, `repos 0` and no further output is the correct result.

- [ ] **Step 4: Confirm the dashboard is closed by default**

Run:

```bash
DEPLOY_MODE=local python3 -c "
from fastapi.testclient import TestClient
from app.main import create_app
c = TestClient(create_app())
print('/dashboard ->', c.get('/dashboard').status_code)
print('/api/dashboard/summary ->', c.get('/api/dashboard/summary').status_code)
"
```

Expected: both print `503` (assuming `DASHBOARD_USER`/`DASHBOARD_PASSWORD` are unset in the environment, which is the default). If they print `200`, credentials are set in your `.env` — unset them and re-run to confirm.

- [ ] **Step 5: Invoke the verification skill**

Per `CLAUDE.md`, any bug fix or logic change requires the **verification** skill before claiming done. Run it and capture PASS evidence.

- [ ] **Step 6: Final commit if anything was adjusted**

```bash
git add -A
git commit -m "test: verify graph perf fixes and fail-closed dashboard auth"
```

---

## Verification Summary

| Finding | Task | Verified by |
|---|---|---|
| P1 SQLite variable-limit cliff | 2 | 3 × `*_variable_limit` tests at 20000 symbols |
| P2 missing `code_edges(target)` index | 1 | `PRAGMA index_list` assertion + pre-existing-db backfill test |
| P3 per-keyword graph rebuild | 7 | `_build_graph` call counter asserts exactly 1 for 3 keywords |
| P4 full subgraph load per save | 4 | `get_subgraph` call counter asserts 0 during `reindex_paths` |
| P5 dashboard materializes every graph | 6 | `get_subgraph` call counter asserts 0 during `project_breakdown` |
| P6 unpaginated scroll | 8 | cursor-following test asserts both pages counted |
| P7 aggressive idle-unload default | 9 | config default assertion |
| Dashboard fail-open auth | 10 | 503 asserted on both routes with credentials unset |
