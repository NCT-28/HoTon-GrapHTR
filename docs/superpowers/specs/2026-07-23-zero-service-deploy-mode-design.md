# Zero-service deploy mode

## Problem

hoton-graphtr currently requires three backing services to run at all: Qdrant
(vector store), Neo4j (code/text-entity graph), Postgres (usage tracking for
`/dashboard`). All three are wired through docker-compose. Running the app
directly on a machine without Docker means standing up all three separately.

The user wants a way to run hoton-graphtr as a single process with zero
external services — file-based storage for all three — while keeping the
existing server-mode (docker) path fully intact for production/shared
deployments.

## Scope

All three backing services (Qdrant, Neo4j, Postgres) get a local/file-based
alternative. Both modes are kept side by side, selected by config — no
existing code (Neo4jGraphStore, PostgresUsageStore, docker-compose.yml) is
removed or changed in behavior.

Out of scope: `searxng`/browser-service (web search, URL ingest) — these are
optional external integrations already injected via `web_search_fn`/
`BrowserClient`, not part of this design. The reasoning LLM and embedding
model are already local (HF `pipeline`, no network service) and need no
change.

## Architecture

One new setting, `DEPLOY_MODE` (`"server"` default | `"local"`), controls all
three storage factories at once — not per-service flags. Each factory
(`get_qdrant_client`, `get_graph_store`, `get_usage_store`) branches on it and
returns a different implementation of the same interface the rest of the app
already depends on (`GraphStore`, `UsageStore`, `QdrantClient`). No other code
— `mcp_server.py`, `repo_watcher.py`, `dashboard/*`, `rag/*` — changes,
because it only ever talks to these interfaces.

```
DEPLOY_MODE=server (default, docker)        DEPLOY_MODE=local (zero-service)
Qdrant server (docker, url=)        -->     QdrantClient(path=graphtr-out/qdrant)   [built-in]
Neo4jGraphStore (bolt://)           -->     SqliteGraphStore(graphtr-out/graph.sqlite) [new]
PostgresUsageStore                  -->     SqliteUsageStore(graphtr-out/usage.sqlite) [new]
```

Local-mode files live under the existing `graphtr-out/` directory (already
used for offline graph snapshot export/query, already partially gitignored)
rather than a new top-level directory.

## Component: Qdrant

`app/clients/qdrant_store.py::get_qdrant_client()` branches on
`settings.deploy_mode`:

- `server` (default): `QdrantClient(url=settings.qdrant_url)` — unchanged.
- `local`: `QdrantClient(path=f"{settings.local_data_dir}/qdrant")` — the
  embedded/on-disk mode already built into `qdrant-client`. No new
  dependency, no new code path in `bootstrap_collections()` or any caller —
  the client object behaves identically either way.

## Component: Neo4j → SQLite

**Key insight:** every graph-traversal algorithm (BFS, shortest-path,
explain-node) already runs in Python via `networkx`, in
`app/graph/graph_query.py`, operating on the full node/edge list returned by
`GraphStore.get_subgraph()`. The storage layer never needs to answer a
Cypher-style traversal query — it only needs to persist and return the right
set of nodes/edges for a `(user_id, repo_id)`. That's plain CRUD, which
SQLite (stdlib `sqlite3`, no new dependency) handles well.

Schema (`graphtr-out/graph.sqlite`):

```sql
repos(user_id, repo_id, source, local_path, last_indexed_at,
      PRIMARY KEY(user_id, repo_id))
code_symbols(id PK, repo_id, user_id, kind, name, file_path,
             start_line, end_line, language)
code_edges(source, target, type)          -- DEFINES/CALLS/IMPORTS/INHERITS
text_entities(id PK, user_id, name, entity_type,
              source_doc_id, source_memory_id)
related_edges(source, target)             -- RELATED_TO
mentions_edges(source, target)            -- TextEntity -> CodeSymbol
```

`replace_repo_graph()` and `delete_repo()` wrap their statements in a single
SQLite transaction (`BEGIN`/`COMMIT`) — real atomicity, simpler than the
Neo4j `session.execute_write()` pattern used for the same guarantee.

New class: `SqliteGraphStore(GraphStore)` in `app/graph/code_graph_store.py`,
implementing every abstract method the interface already defines (mirrors
`FakeGraphStore` in `tests/conftest.py` as a reference for the CRUD shape).

**Alternatives considered and rejected:**

- *networkx + pickle file* — every mutation would require dumping the entire
  graph back to disk; no real transactions, so a crash mid-write can corrupt
  or lose data; a poor fit for `repo_watcher`'s pattern of frequent
  file-change-triggered reindexes (each would rewrite the whole file).
- *DuckDB* — adds a new dependency with no benefit over stdlib `sqlite3` for
  this access pattern (simple point/range lookups, not analytical queries).

## Component: Postgres → SQLite

New class: `SqliteUsageStore(UsageStore)` in `app/dashboard/usage_store.py`,
backed by `graphtr-out/usage.sqlite` (stdlib `sqlite3`), same `usage_events`
schema as the Postgres version.

One behavioral difference: SQLite has no `percentile_cont()`. `counts_by_tool()`
computes p50 latency by fetching `duration_ms` values ordered and taking the
Python-side median instead of doing it in SQL — same result, different layer.

`get_usage_store()` in `local` mode always returns a `SqliteUsageStore` (never
`None`) — unlike the current optional-off behavior when Postgres env vars are
unset — so `/dashboard` stays fully functional in zero-service mode.

Threading: reuse the same `threading.Lock`-around-a-shared-connection pattern
`PostgresUsageStore` already uses (SQLite connections aren't thread-safe by
default either).

## Config wiring

`app/config.py` gains:

```python
deploy_mode: str = "server"          # "server" | "local"
local_data_dir: str = "./graphtr-out"
```

Each of the three factories branches on `settings.deploy_mode` and, in the
`local` branch, `os.makedirs(local_data_dir, exist_ok=True)` before opening
its file. `server`-mode behavior and docker-compose.yml are unchanged.

`.gitignore` gains `graphtr-out/*.sqlite` and `graphtr-out/qdrant/`.

## Testing strategy

Follows the pattern already established in the repo — unit tests that always
run, plus a separate integration-test file gated on a live external service
(see `test_code_graph_store.py` vs `test_code_graph_store_integration.py`,
`test_usage_store.py` vs `test_usage_store_integration.py`):

- `tests/test_sqlite_graph_store.py` — direct tests against
  `SqliteGraphStore(tmp_path / "graph.sqlite")`, mirroring the existing
  behavioral cases in `test_code_graph_store.py` (same interface as
  `FakeGraphStore`/`Neo4jGraphStore`), plus a dedicated case for transaction
  rollback when a statement inside `replace_repo_graph` fails partway
  through. Always runs in CI — no skip, since SQLite needs no external
  service.
- `tests/test_sqlite_usage_store.py` — mirrors `test_usage_store.py`, plus a
  case covering the Python-side median calculation.
- No integration-gated file needed for either — unlike Postgres/Neo4j,
  SQLite *is* the real backend being tested, not a mock standing in for one.
- Qdrant local-path mode: 1–2 tests using
  `QdrantClient(path=tmp_path / "qdrant")` confirming
  `bootstrap_collections()` and upsert/search behave the same as the
  `:memory:` mode already used in `conftest.py`.

## Docs / migration

- `docker/.env.example`: add `DEPLOY_MODE=server` with a comment explaining
  the `local` alternative (writes to `graphtr-out/`, ignores
  `NEO4J_*`/`USAGE_DB_*`/`QDRANT_URL` entirely).
- `README.md`: add a "Run zero-service (no Docker)" section — three steps:
  `pip install -r requirements.txt` → set `DEPLOY_MODE=local` → `uvicorn
  app.main:create_app --factory --port 8030`. No docker-compose, no
  Neo4j/Postgres/Qdrant server needed.
- No automatic migration between modes — `server` and `local` are two
  independent data stores (e.g. one for docker deploy, one for local dev),
  not a data path that syncs between them. State this explicitly in the
  README so it isn't assumed to be a live migration path.
