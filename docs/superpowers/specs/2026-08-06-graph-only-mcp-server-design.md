# Graph-only MCP server design

**Date:** 2026-08-06
**Status:** Approved (pending implementation)

## Problem

`ingest_codebase` (code-graph generation) is pure Python + tree-sitter — it
does not touch the reasoning LLM, the embedding model, Qdrant, or Neo4j.
Despite that, the only way to run it today is through the full server
(`app/main.py:create_app`), which eagerly loads `sentence-transformers`
(`get_embedder()`), imports `torch`/`transformers`, and requires
`qdrant-client`/`neo4j`/`psycopg` to be importable. `install.sh` compounds
this by installing all of `requirements.txt` and pre-downloading both models
unconditionally.

Goal: let a weak local machine (can't run the embedding/reasoning models,
possibly can't even afford to install torch) build a code graph and expose
it to a Claude Code session via the existing `graphtr` MCP skill — without
installing or importing any of the RAG stack's heavy dependencies.

Non-goals: RAG retrieval, TextEntity graph (Neo4j-backed
`entity_extraction.py`/`entity_linker.py`), incremental/watched reindexing,
changes to `docker-graphtr/` or `uninstall.sh`.

## Architecture

Two entrypoints, sharing one graph-generation module, never importing each
other's dependencies:

```
app/main.py              -> create_app()            (existing, full RAG+graph)
app/graph_mcp_server.py  -> create_graph_only_app()  (new, graph only)
        \
         app/graph/ingest.py  (new — extracted from app/mcp_server.py)
              depends on: app/graph/repo_source.py, code_parser.py,
                          snapshot_writer.py  (tree-sitter + stdlib only)
```

## Components

### 1. `app/graph/ingest.py` (new)

Extracted from `app/mcp_server.py`: the `IngestCodebaseResult` model and
`ingest_codebase_impl`. The `ctx: ToolContext` parameter is dropped — the
existing body never reads any `ctx` field, so it was dead weight forcing
callers to construct a full `ToolContext` (embedder/llm/qdrant/graph_store)
just to run a function that needs none of them.

```python
def ingest_codebase_impl(source: str) -> IngestCodebaseResult:
    # same body as today, minus the unused ctx param
```

Imports: `uuid` + the three existing graph submodules. No `app.config`, no
`app.clients.*`. This is the single source of truth for graph generation;
both entrypoints below call it.

### 2. `app/mcp_server.py` (modified)

Replace the local `IngestCodebaseResult`/`ingest_codebase_impl` definitions
with `from app.graph.ingest import ingest_codebase_impl, IngestCodebaseResult`.
The `@mcp.tool() def ingest_codebase(source)` wrapper keeps its
`track_usage(...)` call, now invoking `ingest_codebase_impl(source)` (no
`ctx` arg). Behavior of the full server is unchanged.

### 3. `app/graph_mcp_server.py` (new)

A standalone FastAPI + FastMCP app exposing exactly one tool.

```python
"""Dependency-light MCP server exposing only `ingest_codebase`, for
machines that don't run (or can't install) the RAG stack: no torch,
sentence-transformers, qdrant-client, neo4j, or psycopg required."""
import contextlib

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from app.graph.ingest import IngestCodebaseResult, ingest_codebase_impl


def create_graph_only_app() -> FastAPI:
    mcp = FastMCP("hoton-graphtr-graph-only")

    @mcp.tool()
    def ingest_codebase(source: str) -> IngestCodebaseResult:
        """Parse a local repo path into <repo>/graphtr-out/ (graph.json,
        manifest.json, graphtr.html). One-shot: nothing is kept
        server-side, query the output offline with scripts/query.py. Git
        URLs are not supported -- clone first, pass a path."""
        return ingest_codebase_impl(source)

    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="hoton-graphtr-graph-only", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "graph-only"}

    app.mount("/", mcp_app)
    return app
```

Imports: `fastapi`, `mcp.server.fastmcp`, `app.graph.ingest`. Nothing else.

### 4. `requirements-graph.txt` (new)

```
fastapi==0.139.2
uvicorn[standard]==0.32.1
mcp==1.12.4
tree-sitter-language-pack==1.13.3
```

Versions pinned to match `requirements.txt`. Everything else in
`requirements.txt` (torch, transformers, sentence-transformers,
qdrant-client, neo4j, psycopg, pydantic-settings, networkx, watchdog, ...)
is unused by this path and deliberately excluded.

### 5. `install.sh` (modified — new `--graph-only` flag)

- `pip install -r requirements-graph.txt` instead of `requirements.txt`.
- Skip the "Pre-downloading embedding/reasoning models" block entirely.
- Skip `.env`/`DEPLOY_MODE` creation — `app.config.Settings` is never
  imported on this path.
- `--run` starts `uvicorn app.graph_mcp_server:create_graph_only_app
  --factory --host 0.0.0.0 --port "$PORT"` instead of `app.main:create_app`.
- Venv creation, Python-version detection, target-project skill bootstrap
  (`init_graphtr_skills.py`), and MCP registration (`claude mcp add`) are
  unchanged and shared between both modes — the mounted path (`/mcp`) and
  port behavior are identical between `create_app()` and
  `create_graph_only_app()`.

## Data flow

Unchanged from today: Claude session (via the `graphtr` skill) calls MCP
tool `ingest_codebase(source)` → `resolve_repo_source` → `parse_repo`
(tree-sitter) → `write_graph_snapshot` → `render_viewer` → writes
`<repo>/graphtr-out/{graph.json,manifest.json,graphtr.html}`. Offline
queries continue to use `scripts/query.py` (already stdlib-only,
unaffected).

## Error handling

No new error cases. `ingest_codebase_impl`'s existing behavior (raise
`ValueError` for git URLs, for nonexistent/non-directory paths) moves
as-is into `app/graph/ingest.py`.

## Testing

- `tests/test_ingest_codebase.py`: update the import to
  `from app.graph.ingest import ingest_codebase_impl, IngestCodebaseResult`
  and drop the `ctx`/`ToolContext` argument from every call site
  (`ingest_codebase_impl(str(repo))` instead of
  `ingest_codebase_impl(_ctx(), str(repo))`).
- `tests/test_graph_mcp_server.py` (new): `create_graph_only_app()` builds,
  `GET /health` returns 200 with `mode: "graph-only"`, and the
  `ingest_codebase` tool runs correctly against a small fixture repo.

## Rejected approaches

- **Flag inside the existing `create_app()`:** rejected — `app/mcp_server.py`
  imports `qdrant_client` at module top level, so branching inside
  `create_app()` still forces weak machines to have `qdrant-client`
  installed just to import the module, even on the graph-only branch.
- **Lazy/conditional imports scattered through `app/mcp_server.py`:**
  rejected — smaller diff than a new file, but conditional imports tied to
  a runtime flag are fragile: every future RAG feature added to that file
  has to remember to keep its import inside the right branch.
