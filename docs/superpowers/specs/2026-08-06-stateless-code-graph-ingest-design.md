# Stateless Code-Graph Ingest — Design

## Context

`ingest_codebase` (`app/mcp_server.py::ingest_codebase_impl`) today is a
stateful, multi-call pipeline: it resolves a `source` (local path or git URL,
`repo_source.py::resolve_repo_source`) to a local path, does a first full
parse via `parse_repo`, writes symbols/edges into `GraphStore`
(Neo4j in `server` deploy mode, `SqliteGraphStore`/`LocalMultiRepoGraphStore`
in `local` mode), embeds every symbol into Qdrant
(`CODE_SYMBOL_EMBEDDINGS`), and registers a background
`RepoWatcherManager` watch that keeps reindexing the repo on every file
change for as long as the server process lives. Getting a usable graph out
requires a separate `export_graph_snapshot` call (reads `GraphStore` back)
and a `scripts/build_viewer.py` run to produce `graphtr-out/graphtr.html`.
`query_code_graph` then does BFS/shortest-path/explain against
`GraphStore.get_subgraph` per call.

The `docker-graph/code-repos` bind mount exists solely so the Docker-deployed
hoton-graphtr container can see a host repo's files for the local-path branch
of `resolve_repo_source` — the git-URL branch already clones in-container and
needs no mount.

None of this state is actually needed by the three code-graph MCP tools
themselves: `query_code_graph`'s BFS/path/explain (`graph_query.py`) work
purely off an in-memory `(nodes, edges)` list — no vector search, no
cross-call persistence requirement beyond "give me this repo's current
graph". The DB/watcher machinery exists to make repeated queries cheap by
keeping the graph resident, at the cost of running a live server, minting a
`repo_id` per call to dedupe against, and holding Neo4j/Qdrant state that
outlives any single tool call.

This spec collapses `ingest_codebase` into a single stateless call: parse
once, write the final `graph.json`/`manifest.json`/`graphtr.html` straight
into `<repo>/graphtr-out/`, and return. No server-side state, no watcher, no
Neo4j/Qdrant writes for code-graph data. Subsequent queries use the
already-documented offline path (`scripts/query.py`, `graphtr.html`) against
that output — no MCP round trip needed.

## Scope

In scope: `ingest_codebase`, `query_code_graph`, `export_graph_snapshot`,
`RepoWatcherManager`, and the code-graph-specific surface of `GraphStore`
(`code_graph_store.py`, all three implementations).

Out of scope, unchanged: RAG retrieval (`retrieve_chunks`, `get_rag_context`),
memory/profile tools, the dashboard, usage tracking, `DEPLOY_MODE`
server/local dispatch for everything except the code-graph store, and the
`code-repos` bind-mount mechanism itself (still needed for Docker-deployed
local-path ingest; unaffected by this spec).

**Accepted regression:** `entity_linker.py::link_entities_to_code` (RAG
document-ingest pipeline, `graph_pipeline.py`) reads
`graph_store.list_code_symbols(user_id)` to link extracted text entities to
previously-ingested code symbols. Once `ingest_codebase` stops writing code
symbols into `GraphStore`, this always returns empty — entity→code linking
becomes a permanent no-op. `list_code_symbols` stays on `GraphStore` (RAG
still calls it), just never populated. No fix attempted here; documented as
a known consequence.

## Non-goals

- No migration of previously-ingested repos' Neo4j/Qdrant code-graph data —
  it's simply orphaned. Anyone relying on it re-runs the new `ingest_codebase`
  to get a fresh `graphtr-out/`.
- No change to `resolve_repo_source` / the `code-repos` mount mechanism —
  still how a Docker-deployed server sees a host repo's files for the
  local-path branch.
- No change to RAG's `GraphStore` usage (`upsert_text_entities`,
  `list_text_entities`, `upsert_mentions_edges`, `upsert_related_edges`,
  `delete_text_entities_by_source_doc`, `ping`) — kept exactly as is.
- No attempt to keep `entity_linker`'s code-symbol linking alive (see
  Accepted regression above).

## Design

### 1. `ingest_codebase` becomes one-shot (`mcp_server.py`)

New signature: `ingest_codebase(source: str) -> IngestCodebaseResult`. Drops
`user_id` — there is no DB to scope by tenant against; the output already
lives inside the target repo's own directory.

Body:

```
repo_id = str(uuid.uuid4())            # fresh every call, no existing-repo dedupe (no DB to check)
local_path = resolve_repo_source(source, repo_id)   # unchanged
symbols, edges = parse_repo(repo_id, local_path)     # unchanged
write_graph_snapshot(local_path, repo_id, symbols, edges)  # new — see below
build(os.path.join(local_path, "graphtr-out"))              # scripts/build_viewer.py, see §3
return IngestCodebaseResult(repo_id=repo_id, symbol_count=len(symbols), edge_count=len(edges))
```

`write_graph_snapshot` is the current `export_graph_snapshot_impl`'s
node-kind/edge-type tallying and `GraphNodeOut`/`GraphEdgeOut` serialization,
retargeted to take `symbols`/`edges` directly instead of reading them back
from `graph_store.get_subgraph`, and to write `graph.json` + `manifest.json`
to `<local_path>/graphtr-out/` instead of returning them over MCP. No
`code_symbol_count` field in `manifest.json` — that stat came from
`count_code_symbol_embeddings` against Qdrant, and code symbols are no
longer embedded (§2).

`track_usage(ctx.usage_store, "ingest_codebase", ...)` stays (dashboard/usage
tracking unaffected) — the second positional arg (`user_id`) drops to `""`
since the tool no longer takes one.

### 2. Remove `query_code_graph`, `export_graph_snapshot`

Both become redundant: querying a written `graph.json` already has an
offline path documented in the `graphtr` skill (`scripts/query.py
--out-dir graphtr-out query|path|explain`, pure stdlib, no MCP round trip),
and `graphtr.html` is a static viewer over the same file. Delete both
`@mcp.tool()` registrations and their `_impl` functions from `mcp_server.py`.

### 3. Remove `RepoWatcherManager` (`app/graph/repo_watcher.py`)

Deleted entirely. It existed only to keep a watched repo's `GraphStore` state
+ Qdrant `CODE_SYMBOL_EMBEDDINGS` vectors in sync with disk across the
server's lifetime — with no watcher and no `GraphStore` writes, both its
`watch()`/`reindex()`/`reindex_paths()` responsibilities and its Qdrant
embed/re-embed calls (`_replace_symbol_embeddings`,
`_update_symbol_embeddings`) have no caller left. Code symbols are no longer
embedded into Qdrant at all — `query_code_graph`'s BFS/path/explain never
used vector search (§ Context), so nothing downstream needs it once that
tool is gone.

Wiring cleanup:
- `main.py` — remove `watcher_manager` param, the `RepoWatcherManager` import
  and construction, `resolved_watcher_manager.resume_all()` at startup and
  `.stop()` at shutdown.
- `mcp_server.py` — remove `watcher_manager` field from `ToolContext` and
  `build_tool_context`.

### 4. `scripts/build_viewer.py` — extract a callable `build(out_dir)`

Today `build_viewer.py` is CLI-only (`main()` parses `--out-dir` and does the
work inline). Extract the body into `build(out_dir: str) -> None`, with
`main()` becoming a thin `argparse` wrapper calling it. `mcp_server.py`
imports and calls `build()` directly after `write_graph_snapshot` — avoids
duplicating the `graph.json` → `graphtr.html` rendering logic, and keeps the
standalone CLI usage (`python3 scripts/build_viewer.py --out-dir
graphtr-out`, still valid for regenerating the viewer without re-ingesting)
working unchanged.

### 5. `GraphStore` (`code_graph_store.py`) — drop the code-graph surface

Remove from all three implementations (`Neo4jGraphStore`, `SqliteGraphStore`,
`LocalMultiRepoGraphStore`) and the abstract base:
`upsert_repo`, `upsert_symbols`, `upsert_code_edges`, `delete_repo`,
`replace_repo_graph`, `replace_files_in_repo`, `get_repo`, `list_repos`,
`get_subgraph`, `list_symbol_index`, `list_symbol_ids`, `count_subgraph`,
`count_mentioning_text_entities`, `get_mentioning_text_entities`. These are
orphaned the moment `ingest_codebase`/`RepoWatcherManager` stop calling them
— no other caller exists (verified: only `mcp_server.py`'s graph tools and
`repo_watcher.py` call them).

Keep unchanged: `upsert_text_entities`, `upsert_related_edges`,
`upsert_mentions_edges`, `list_text_entities`, `list_code_symbols`,
`delete_text_entities_by_source_doc`, `ping` — still used by
`entity_extraction.py`/`entity_linker.py` (RAG document-ingest,
`graph_pipeline.py`). `list_code_symbols` keeps its signature; it just always
returns `[]` now (§ Scope, accepted regression).

`tests/conftest.py::FakeGraphStore` — trim to match: drop its code-graph
method stubs, keep the TextEntity ones RAG tests exercise.

### 6. Test files

Deleted (code-graph-only coverage, no longer applicable):
`test_repo_watcher.py`, `test_main_watcher_wiring.py`,
`test_mcp_graph_tools.py`, `test_graph_query.py`,
`test_code_graph_store_integration.py`, `test_sqlite_graph_store.py`,
`test_local_multi_repo_graph_store.py`.

New: a test for the rewritten `ingest_codebase` — point it at a small fixture
repo under a tmp dir, assert `graphtr-out/graph.json`,
`graphtr-out/manifest.json`, `graphtr-out/graphtr.html` exist and
`graph.json`'s node/edge counts match the returned `IngestCodebaseResult`.

Updated (call-site only, not deleted — these cover usage tracking /
dashboard, unrelated to graph storage): `test_usage_store.py`,
`test_sqlite_usage_store.py`, `test_mcp_tools_usage_tracking.py`,
`test_tracker.py`, `test_dashboard_queries.py` — drop the `user_id` arg from
`ingest_codebase` calls.

### 7. Docs

- `.claude/skills/graphtr/SKILL.md` — Bootstrap collapses to one
  `ingest_codebase(source)` call (no `export_graph_snapshot` +
  `build_viewer.py` steps); Refresh collapses to "re-run `ingest_codebase`"
  (no more watcher-based auto-reindex, so drop the "wait for the poll-based
  watcher" guidance and the `ingest_codebase` re-run warning about minting a
  duplicate `repo_id`/watcher — every call is expected to mint a fresh
  `repo_id` now, that's no longer a mistake to avoid). Keep the `code-repos`
  mount guidance for Docker-deployed local-path ingest (§ Scope, unchanged).
- `CLAUDE.md` architecture table — "Code graph" row: `server` column becomes
  "n/a (stateless, writes to repo's own `graphtr-out/`)", drop the
  `code_graph_store.py` dispatch-point reference for the removed methods
  (keep it for the TextEntity methods that remain).

## Rollout

No feature flag / gradual rollout — this is a breaking change to the
`ingest_codebase`/`query_code_graph`/`export_graph_snapshot` MCP surface
with no migration path for existing Neo4j/Qdrant-resident code graphs
(Non-goals). Ship as one change; anyone with an existing ingested repo
re-runs the new `ingest_codebase` to get a `graphtr-out/`.
