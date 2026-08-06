# Stateless Code-Graph Ingest — Design

## Context

`ingest_codebase` (`app/mcp_server.py::ingest_codebase_impl`) today is a
stateful, multi-call pipeline: it resolves a `source` (local path or git URL,
`repo_source.py::resolve_repo_source`) to a local path, delegates to
`RepoWatcherManager.reindex()` — which does a full `parse_repo`, writes
symbols/edges into `GraphStore` (Neo4j in `server` deploy mode,
`SqliteGraphStore`/`LocalMultiRepoGraphStore` in `local` mode) and embeds
every symbol into Qdrant (`CODE_SYMBOL_EMBEDDINGS`) — and registers a
background watch that keeps reindexing the repo on every file change for as
long as the server process lives. Getting a usable graph out requires a
separate `export_graph_snapshot` call (reads `GraphStore` back) and a
`scripts/build_viewer.py` run to produce `graphtr-out/graphtr.html`.
`query_code_graph` then does BFS/shortest-path/explain against
`GraphStore.get_subgraph` per call.

The `docker-graph/code-repos` bind mount exists solely so the Docker-deployed
hoton-graphtr container can see a host repo's files for the local-path branch
of `resolve_repo_source`.

None of this state is needed by the three code-graph MCP tools themselves:
`query_code_graph`'s BFS/path/explain (`graph_query.py`) work purely off an
in-memory `(nodes, edges)` list — no vector search, no cross-call persistence
requirement beyond "give me this repo's current graph". The DB/watcher
machinery exists to make repeated queries cheap by keeping the graph
resident, at the cost of running a live server, minting a `repo_id` per call
to dedupe against, and holding Neo4j/Qdrant state that outlives any single
tool call.

This spec collapses `ingest_codebase` into a single stateless call: parse
once, write the final `graph.json`/`manifest.json`/`graphtr.html` straight
into `<repo>/graphtr-out/`, and return. No server-side state, no watcher, no
Neo4j/Qdrant writes for code-graph data. Subsequent queries use the
already-documented offline path (`scripts/query.py`, `graphtr.html`) against
that output — no MCP round trip needed.

## Scope

In scope: `ingest_codebase`, `query_code_graph`, `export_graph_snapshot`,
`RepoWatcherManager`, the graph-fusion branch of `get_rag_context`, the
code-graph reads in `app/dashboard/queries.py`, and the code-graph-specific
surface of `GraphStore` (`code_graph_store.py`, all three implementations).

Out of scope, unchanged: `retrieve_chunks`, memory/profile tools, document
ingest, usage tracking as a mechanism, `DEPLOY_MODE` server/local dispatch
for everything except the code-graph store, and the `code-repos` bind-mount
mechanism itself (still needed for Docker-deployed local-path ingest).

### Accepted regression 1 — entity→code linking

`entity_linker.py::link_entities_to_code` (RAG document-ingest pipeline,
`graph_pipeline.py`) reads `graph_store.list_code_symbols(user_id)` to link
extracted text entities to previously-ingested code symbols. Once
`ingest_codebase` stops writing code symbols into `GraphStore`, this always
returns empty — entity→code linking becomes a permanent no-op.
`list_code_symbols` stays on `GraphStore` (RAG still calls it), just never
populated. No fix attempted here.

### Accepted regression 2 — hybrid graph-RAG fusion is removed

`get_rag_context_impl` (`mcp_server.py:183-190`) enriches its context with a
depth-1 BFS over the repo's code graph:
`extract_graph_keywords` → `fuse_graph_context(ctx.graph_store, ...)` →
`GraphStore.get_subgraph`. That is a live consumer of the very surface §5
deletes, so the feature (spec
`2026-07-24-hybrid-graph-rag-fusion-design.md`) cannot survive a stateless
code graph without re-plumbing `get_rag_context` to take a repo *path* and
read `graph.json` off disk. Decision: **remove the fusion path entirely**
rather than leave it silently returning nothing (see §6).

## Non-goals

- No migration of previously-ingested repos' Neo4j/Qdrant code-graph data —
  it's simply orphaned. Anyone relying on it re-runs the new `ingest_codebase`
  to get a fresh `graphtr-out/`.
- No change to `resolve_repo_source`'s local-path branch or the `code-repos`
  mount mechanism — still how a Docker-deployed server sees a host repo's
  files.
- No change to RAG's `GraphStore` usage (`upsert_text_entities`,
  `list_text_entities`, `upsert_mentions_edges`, `upsert_related_edges`,
  `delete_text_entities_by_source_doc`, `ping`) — kept exactly as is.
- No attempt to keep `entity_linker`'s code-symbol linking alive, or to
  reimplement graph fusion against `graph.json` (both accepted regressions
  above).

## Design

### 1. `ingest_codebase` becomes one-shot, local-path only (`mcp_server.py`)

New signature: `ingest_codebase(source: str) -> IngestCodebaseResult`. Drops
`user_id` — there is no DB to scope by tenant against; the output already
lives inside the target repo's own directory.

**Git URLs are no longer accepted.** `resolve_repo_source`'s git branch
clones into `settings.code_repos_dir/<repo_id>` *inside the container*, so
writing `graphtr-out/` under that path produces output the caller cannot
read; and because every call now mints a fresh `repo_id` (no DB to dedupe
against), the old `rmtree(dest)`-then-reclone reuse is gone and each ingest
would leak a full clone directory forever. `ingest_codebase` raises
`ValueError("git URLs are not supported; clone the repo and pass a local path")`
for `http://`/`https://` sources before calling `resolve_repo_source`.
`resolve_repo_source` itself keeps both branches (see §8) — only this tool
refuses the URL form.

Body:

```
if source.startswith(("http://", "https://")):
    raise ValueError("git URLs are not supported; clone the repo and pass a local path")
repo_id = str(uuid.uuid4())                          # fresh every call, no dedupe (no DB to check)
local_path = resolve_repo_source(source, repo_id)    # local-path branch only
symbols, edges = parse_repo(repo_id, local_path)     # unchanged
write_graph_snapshot(local_path, repo_id, symbols, edges)   # new — see below
build(os.path.join(local_path, "graphtr-out"))              # scripts/build_viewer.py, see §4
return IngestCodebaseResult(repo_id=repo_id, symbol_count=len(symbols), edge_count=len(edges))
```

`write_graph_snapshot` is the current `export_graph_snapshot_impl`'s
node-kind/edge-type tallying and `GraphNodeOut`/`GraphEdgeOut` serialization,
retargeted to take `symbols`/`edges` directly instead of reading them back
from `graph_store.get_subgraph`, and to write `graph.json` + `manifest.json`
to `<local_path>/graphtr-out/` instead of returning them over MCP. Two
constraints on it:

- **`manifest.json` must be merged, not overwritten.**
  `scripts/index_knowledge.py:41` mints a `rag_user_id` *into* that same
  file on first run; a blind write destroys it and the next knowledge index
  mints a new user, orphaning the previously indexed docs. Read the existing
  manifest if present, update only the keys this writer owns
  (`repo_id`, `node_count`, `edge_count`, `node_kinds`, `edge_types`,
  `last_indexed_at`), and write the merged dict back.
- **No `code_symbol_count` field** — that stat came from
  `count_code_symbol_embeddings` against Qdrant, and code symbols are no
  longer embedded (§3).

`track_usage(ctx.usage_store, "ingest_codebase", ...)` stays (dashboard/usage
tracking unaffected) — the second positional arg (`user_id`) drops to `""`
since the tool no longer takes one. Consequence to accept: the dashboard's
per-user breakdown (`counts_by_user`) attributes every ingest to the empty
user.

**`symbol_count` semantics change.** It used to be `len(nodes)` from
`get_subgraph`, which folds in MENTIONS-linked text-entity nodes; it is now
`len(symbols)` straight from `parse_repo`. Same repo, different number — the
new value is the parsed-symbol count only.

### 2. Remove `query_code_graph`, `export_graph_snapshot`

Both become redundant: querying a written `graph.json` already has an
offline path documented in the `graphtr` skill (`scripts/query.py
--out-dir graphtr-out query|path|explain`, pure stdlib, no MCP round trip),
and `graphtr.html` is a static viewer over the same file. Delete both
`@mcp.tool()` registrations and their `_impl` functions from `mcp_server.py`,
plus the now-orphaned `QueryCodeGraphResult` and `GraphSnapshotResult`
models. `GraphNodeOut`/`GraphEdgeOut`/`_to_node_out` stay —
`write_graph_snapshot` still uses them to serialize `graph.json`.

Remove `app/dashboard/tracker.py`'s `MCP_TOOL_NAMES` entries for
`query_code_graph` and `export_graph_snapshot`; leaving them makes the
dashboard's tool filter carry names no tool can emit.

### 3. Remove `RepoWatcherManager` (`app/graph/repo_watcher.py`)

Deleted entirely. It existed only to keep a watched repo's `GraphStore` state
+ Qdrant `CODE_SYMBOL_EMBEDDINGS` vectors in sync with disk across the
server's lifetime — with no watcher and no `GraphStore` writes, both its
`watch()`/`reindex()`/`reindex_paths()` responsibilities and its Qdrant
embed/re-embed calls (`_replace_symbol_embeddings`,
`_update_symbol_embeddings`) have no caller left. Code symbols are no longer
embedded into Qdrant at all.

Wiring cleanup:
- `main.py` — remove `watcher_manager` param, the `RepoWatcherManager` import
  and construction, the `use_repo_resolver` computation that only feeds it,
  `resolved_watcher_manager.resume_all()` at startup and `.stop()` at
  shutdown.
- `mcp_server.py` — remove `watcher_manager` field from `ToolContext` and
  `build_tool_context`.

`app/clients/qdrant_store.py` keeps `CODE_SYMBOL_EMBEDDINGS`,
`bootstrap_collections`' creation of it, `symbol_point_id`, and
`get_repo_qdrant_client` — the collection is left in place but never written.
`count_code_symbol_embeddings` is deleted along with its two callers (§2, §5).

### 4. `scripts/build_viewer.py` — extract a callable `build(out_dir)`

Today `build_viewer.py` is CLI-only: `main()` hand-parses
`sys.argv` (`if len(args) >= 2 and args[0] == "--out-dir"`, line 112-116) —
there is no `argparse` in the file. Extract everything after that parsing
into `build(out_dir: Path) -> None`, leaving `main()` as the same hand-rolled
arg parse plus a `build(out_dir)` call. Do **not** introduce `argparse`: it
would change `--help`/unknown-arg behavior for a CLI that isn't in scope
here. `mcp_server.py` imports and calls `build()` directly after
`write_graph_snapshot` — avoids duplicating the `graph.json` → `graphtr.html`
rendering logic, and keeps standalone usage (`python3
scripts/build_viewer.py --out-dir graphtr-out`) working unchanged.

### 5. Dashboard (`app/dashboard/queries.py`) — drop the code-graph panels

`queries.py` is a live consumer of the surface §6 deletes, contrary to what a
first pass suggests:

- `project_breakdown(graph_store)` (lines 47-62) is built entirely on
  `list_repos()` + `count_subgraph()`. With no repo registry it has no data
  source. **Delete the function**, its call site in the dashboard router, and
  the "projects" section of the summary response.
- `_code_symbol_embeddings_count_across_repos` (lines 16-21) fans
  `count_code_symbol_embeddings` out over `list_repos()`. **Delete it**, drop
  the `fan_out_code_symbols` branch from `storage_breakdown`, and drop
  `CODE_SYMBOL_EMBEDDINGS` from `_COLLECTIONS` — the collection exists but is
  permanently empty, so reporting it as a 0-point row is noise.
- With both gone, `storage_breakdown` no longer needs its `graph_store`
  parameter; drop it and update the router call.

`app/clients/qdrant_store.py::count_code_symbol_embeddings` loses its last
caller here and in §2, and is deleted with them — including its
`graph_store.get_repo(...)` local-mode branch (line 92), the third caller of
the removed `GraphStore` surface.

### 6. Remove the graph-fusion path from `get_rag_context`

Per Accepted regression 2. Delete:

- `mcp_server.py:183-190` — the `graph_nodes`/`graph_edges` locals, the
  `if repo_id and ctx.graph_store:` block, and the `extract_graph_keywords` /
  `fuse_graph_context` imports. `build_full_context(chunks, memories,
  profile)` is called without graph args.
- `get_rag_context`'s `repo_id` parameter (and the `repo_id=` kwarg passed to
  its `track_usage`) — it had no other use. This is a second breaking MCP
  signature change; acceptable in the same ship (see Rollout).
- `app/agentic/graph_fusion.py` — `extract_graph_keywords` has no other
  caller.
- `app/graph/graph_query.py::fuse_graph_context` — with `query_code_graph`
  gone too, the whole module is orphaned (`bfs_query`, `shortest_path`,
  `explain_node`, and the private helpers had no callers outside it). Delete
  `app/graph/graph_query.py`. The offline equivalents live in
  `scripts/query.py`, which is standalone stdlib and untouched.
- `app/rag/context.py::build_graph_context_section` and the
  `graph_nodes`/`graph_edges` parameters of `build_full_context` — orphaned
  by the above.

### 7. `GraphStore` (`code_graph_store.py`) — drop the code-graph surface

Remove from all three implementations (`Neo4jGraphStore`, `SqliteGraphStore`,
`LocalMultiRepoGraphStore`) and the abstract base:
`upsert_repo`, `upsert_symbols`, `upsert_code_edges`, `delete_repo`,
`replace_repo_graph`, `replace_files_in_repo`, `get_repo`, `list_repos`,
`get_subgraph`, `list_symbol_index`, `list_symbol_ids`, `count_subgraph`,
`count_mentioning_text_entities`, `get_mentioning_text_entities`.

Callers, all removed by §1-§6: `mcp_server.py`'s graph tools and fusion
branch, `repo_watcher.py`, `graph_query.py`, `dashboard/queries.py`,
`qdrant_store.py::count_code_symbol_embeddings`.

**`LocalMultiRepoGraphStore` is deleted, not trimmed.** The class exists only
to route each repo's code symbols to its own `<local_path>/graphtr-out/graph.sqlite`
via `list_repos()` + `_repo_store(repo["local_path"])`. Strip the code-graph
surface and every remaining method is a one-line delegation to
`self._central` — i.e. it becomes `SqliteGraphStore` with extra indirection.
`get_graph_store()`'s local branch returns
`SqliteGraphStore(<local_data_dir>/graph.sqlite)` directly instead.
`SqliteGraphStore.list_code_symbols` keeps reading a `code_symbols` table
nothing writes, so it returns `[]` — Accepted regression 1. The table DDL in
`_init_schema` stays: dropping it would change on-disk schema for existing
installs for no benefit.

Keep unchanged: `upsert_text_entities`, `upsert_related_edges`,
`upsert_mentions_edges`, `list_text_entities`, `list_code_symbols`,
`delete_text_entities_by_source_doc`, `ping` — still used by
`entity_extraction.py`/`entity_linker.py` (RAG document-ingest,
`graph_pipeline.py`).

`tests/conftest.py::FakeGraphStore` — trim to match: drop its code-graph
method stubs, keep the TextEntity ones RAG tests exercise. Note its
`get_subgraph` deliberately merges MENTIONS edges + text-entity nodes into
the code subgraph; that shape existed only for the fusion path and goes with
it.

### 8. `repo_source.py` — unchanged

`resolve_repo_source` keeps both branches as-is. §1 rejects git URLs at the
`ingest_codebase` boundary rather than here, so `test_repo_source.py`'s
clone/SSRF coverage (`test_resolve_repo_source_clones_public_git_url`,
`test_resolve_repo_source_rejects_private_git_url`) stays valid and the
function remains reusable. The trade-off: the git branch has no production
caller after this change. Flagged, not deleted — deleting it is a separate
call.

### 9. Test files

**Deleted** (cover code the change removes):
`test_repo_watcher.py`, `test_main_watcher_wiring.py`,
`test_mcp_graph_tools.py`, `test_graph_query.py`, `test_code_graph_store.py`,
`test_code_graph_store_integration.py`, `test_sqlite_graph_store.py`,
`test_local_multi_repo_graph_store.py`, `test_graph_fusion.py`.

**Rewritten** (they use the removed surface for *setup*, so dropping a
`user_id` arg is not enough):
- `test_mcp_tools.py:162-200` — the three `get_rag_context` fusion tests
  (`..._fuses_graph_when_repo_id_given`,
  `..._skips_graph_fusion_without_repo_id`,
  `..._graph_fusion_skipped_when_no_keywords_extracted`) delete with the
  feature; the surviving tests drop the `graph_store.upsert_symbols(...)`
  setup and the `graph_keywords=` LLM fake knob.
- `test_entity_linker.py:33,65` — `upsert_symbols` setup. With
  `list_code_symbols` permanently empty, these tests assert the no-op
  (Accepted regression 1) instead of the linking behavior.
- `test_dashboard_queries.py` — drops every `upsert_repo`/`upsert_symbols`/
  `upsert_code_edges` fixture, the `project_breakdown` tests, and the
  `get_subgraph`-call-counting test; keeps the usage-event aggregation tests.
- `test_mcp_tools_usage_tracking.py:6` — `upsert_symbols` setup plus the
  `ingest_codebase` call-site signature.
- `test_context.py:112-114` — `build_full_context(..., graph_nodes=...)`
  goes with §6.
- `test_qdrant_store.py` — keeps the `CODE_SYMBOL_EMBEDDINGS` bootstrap
  assertions (collection still created), drops anything asserting it gets
  populated.

**Call-site only** (usage tracking / dashboard, unrelated to graph storage):
`test_usage_store.py`, `test_sqlite_usage_store.py`, `test_tracker.py` —
drop the `user_id` arg from `ingest_codebase` references and the two removed
tool names from `MCP_TOOL_NAMES` expectations.

**New**: a test for the rewritten `ingest_codebase` — point it at a small
fixture repo under a tmp dir, assert `graphtr-out/graph.json`,
`graphtr-out/manifest.json`, `graphtr-out/graphtr.html` exist and
`graph.json`'s node/edge counts match the returned `IngestCodebaseResult`.
Plus two more: a git-URL `source` raises `ValueError`, and an existing
`manifest.json` carrying a `rag_user_id` still has it after ingest.

### 10. Docs

- `.claude/skills/graphtr/SKILL.md` — Bootstrap collapses to one
  `ingest_codebase(source)` call (no `export_graph_snapshot` +
  `build_viewer.py` steps); Refresh collapses to "re-run `ingest_codebase`"
  (no watcher, so drop the "wait for the poll-based watcher" guidance and
  the re-run warning about minting a duplicate `repo_id`/watcher — every call
  mints a fresh `repo_id` now, that's expected, not a mistake). Drop the
  `query_code_graph` MCP fallback (line 43) — `scripts/query.py` is the only
  query path. Add: git URLs are rejected, pass a local path. Keep the
  `code-repos` mount guidance for Docker-deployed ingest.
- `CLAUDE.md` architecture table — "Code graph" row: `server` column becomes
  "n/a (stateless, writes to repo's own `graphtr-out/`)", drop the
  `code_graph_store.py` dispatch-point reference for the removed methods
  (keep it for the TextEntity methods that remain). The `app/graph/` module
  description drops `graph_query.py` and `repo_watcher.py`.
- `README.md` / dashboard docs — drop the projects panel if described.

## Rollout

No feature flag / gradual rollout. Three breaking MCP signature changes ship
together: `ingest_codebase(source)` (no `user_id`, no git URLs),
`get_rag_context(user_id, query)` (no `repo_id`), and the removal of
`query_code_graph`/`export_graph_snapshot`. No migration path for existing
Neo4j/Qdrant-resident code graphs (Non-goals) — anyone with an ingested repo
re-runs the new `ingest_codebase` to get a `graphtr-out/`.

## Verification

1. `pytest` green after §9 → verify: no test references a removed
   `GraphStore` method.
2. `grep -rn 'get_subgraph\|list_repos\|count_subgraph\|upsert_symbols\|fuse_graph_context\|watcher_manager' app/ scripts/`
   returns nothing → verify: no orphaned caller survived.
3. `DEPLOY_MODE=local` server up, `ingest_codebase` against a small repo →
   verify: `graphtr-out/{graph.json,manifest.json,graphtr.html}` written,
   counts match the tool result, a pre-existing `rag_user_id` in
   `manifest.json` survives.
4. `GET /api/dashboard/summary` with the dashboard creds set → verify: 200,
   no projects panel, no `code_symbol_embeddings` row, usage sections intact.
5. `get_rag_context` over an indexed doc set → verify: still returns chunks
   (no graph section, no `repo_id` arg).
