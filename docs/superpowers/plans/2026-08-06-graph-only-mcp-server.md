# Graph-only MCP server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a weak machine build a code graph via the `graphtr` MCP skill without installing or importing any RAG-stack dependency (torch, transformers, sentence-transformers, qdrant-client, neo4j, psycopg).

**Architecture:** Extract the already-dependency-free `ingest_codebase` logic out of `app/mcp_server.py` into a new `app/graph/ingest.py` module, then add a second, minimal FastAPI+FastMCP entrypoint (`app/graph_mcp_server.py`) that imports only that module. `app/main.py`'s existing server keeps working unchanged, now sourcing the same logic from the shared module. `install.sh` gets a `--graph-only` flag that installs a trimmed `requirements-graph.txt` and boots the new entrypoint instead.

**Tech Stack:** Python 3.10+, FastAPI, `mcp` (FastMCP), `tree-sitter-language-pack`, pytest.

## Global Constraints

- `requirements-graph.txt` pins the exact same versions as `requirements.txt` for shared packages: `fastapi==0.139.2`, `uvicorn[standard]==0.32.1`, `mcp==1.12.4`, `tree-sitter-language-pack==1.13.3`.
- `app/graph/ingest.py` must import nothing beyond `uuid` and the existing `app.graph.{repo_source,code_parser,snapshot_writer}` modules — no `app.config`, no `app.clients.*`.
- `app/graph_mcp_server.py` must import nothing beyond `fastapi`, `mcp.server.fastmcp`, `contextlib`, and `app.graph.ingest` — no `app.config`, no `app.clients.*`, no `app.rag.*`, no `app.agentic.*`, no `qdrant_client`.
- Existing full-server behavior (`app/main.py:create_app`, `app/mcp_server.py`) must not change observably — same tool docstrings, same `track_usage` wrapping, same error messages.

---

### Task 1: Extract `ingest_codebase_impl` into `app/graph/ingest.py`

**Files:**
- Create: `app/graph/ingest.py`
- Modify: `app/mcp_server.py:96-99` (remove `IngestCodebaseResult`), `app/mcp_server.py:172-184` (remove `ingest_codebase_impl`), `app/mcp_server.py:1-24` (imports), `app/mcp_server.py:220-226` (tool wrapper call site)
- Modify: `tests/test_ingest_codebase.py` (import path + drop `ctx` arg)

**Interfaces:**
- Produces: `app.graph.ingest.IngestCodebaseResult` (pydantic `BaseModel`, fields `repo_id: str`, `symbol_count: int`, `edge_count: int`) and `app.graph.ingest.ingest_codebase_impl(source: str) -> IngestCodebaseResult`, raising `ValueError` for `http://`/`https://` sources or paths `resolve_repo_source` rejects.

- [ ] **Step 1: Write the failing test for the new module location**

Replace the whole content of `tests/test_ingest_codebase.py`:

```python
import json

import pytest

from app.graph.ingest import IngestCodebaseResult, ingest_codebase_impl


def _fixture_repo(tmp_path):
    (tmp_path / "mod.py").write_text(
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def caller():\n"
        "    return helper()\n"
    )
    return tmp_path


def test_ingest_codebase_writes_graph_manifest_and_viewer(tmp_path):
    repo = _fixture_repo(tmp_path)

    result = ingest_codebase_impl(str(repo))

    out_dir = repo / "graphtr-out"
    assert isinstance(result, IngestCodebaseResult)
    assert (out_dir / "graph.json").exists()
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "graphtr.html").exists()


def test_ingest_codebase_result_counts_match_graph_json(tmp_path):
    repo = _fixture_repo(tmp_path)

    result = ingest_codebase_impl(str(repo))

    graph = json.loads((repo / "graphtr-out" / "graph.json").read_text())
    assert result.symbol_count == len(graph["nodes"])
    assert result.edge_count == len(graph["edges"])
    assert result.symbol_count > 0


def test_ingest_codebase_mints_a_fresh_repo_id_every_call(tmp_path):
    repo = _fixture_repo(tmp_path)

    first = ingest_codebase_impl(str(repo))
    second = ingest_codebase_impl(str(repo))

    assert first.repo_id != second.repo_id


def test_ingest_codebase_rejects_git_urls(tmp_path):
    with pytest.raises(ValueError, match="git URLs are not supported"):
        ingest_codebase_impl("https://github.com/example/repo.git")


def test_ingest_codebase_preserves_rag_user_id_in_manifest(tmp_path):
    repo = _fixture_repo(tmp_path)
    out_dir = repo / "graphtr-out"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(json.dumps({"rag_user_id": "keep-me"}))

    ingest_codebase_impl(str(repo))

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["rag_user_id"] == "keep-me"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest_codebase.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.graph.ingest'` (or `ImportError`).

- [ ] **Step 3: Create `app/graph/ingest.py`**

```python
"""Code-graph ingestion: parse a local repo with tree-sitter and write the
result to <repo>/graphtr-out/. No LLM, no embedding model, no Qdrant/Neo4j —
this module's only dependencies are the graph submodules below, so it can be
imported on a machine that has none of the RAG stack installed."""
import uuid

from pydantic import BaseModel

from app.graph.code_parser import parse_repo
from app.graph.repo_source import resolve_repo_source
from app.graph.snapshot_writer import render_viewer, write_graph_snapshot


class IngestCodebaseResult(BaseModel):
    repo_id: str
    symbol_count: int
    edge_count: int


def ingest_codebase_impl(source: str) -> IngestCodebaseResult:
    # Git URLs would clone into the container and the graphtr-out/ written there
    # would be unreachable to the caller -- and with a fresh repo_id per call,
    # every clone would leak a new directory.
    if source.startswith(("http://", "https://")):
        raise ValueError("git URLs are not supported; clone the repo and pass a local path")

    repo_id = str(uuid.uuid4())
    local_path = resolve_repo_source(source)
    symbols, edges = parse_repo(repo_id, local_path)
    out_dir = write_graph_snapshot(local_path, repo_id, symbols, edges)
    render_viewer(out_dir)
    return IngestCodebaseResult(repo_id=repo_id, symbol_count=len(symbols), edge_count=len(edges))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest_codebase.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Update `app/mcp_server.py` to consume the extracted module**

In `app/mcp_server.py`:

1. Add to the import block (after the existing `app.graph.*` imports on lines 19-22):
```python
from app.graph.ingest import IngestCodebaseResult, ingest_codebase_impl
```
2. Remove the now-unused imports that only `ingest_codebase_impl` needed: `from app.graph.code_parser import parse_repo` (line 20), `from app.graph.repo_source import resolve_repo_source` (line 21), `from app.graph.snapshot_writer import render_viewer, write_graph_snapshot` (line 22). Keep `from app.graph.code_graph_store import GraphStore` (line 19, still used by `ToolContext`).
3. Delete the `class IngestCodebaseResult(BaseModel):` block (lines 96-99).
4. Delete the `def ingest_codebase_impl(ctx: ToolContext, source: str) -> IngestCodebaseResult:` function (lines 172-184).
5. In `build_mcp_server`, change the tool body (around line 226) from `return ingest_codebase_impl(ctx, source)` to `return ingest_codebase_impl(source)`.

- [ ] **Step 6: Run the full test suite to confirm nothing else broke**

Run: `pytest -v`
Expected: PASS, no failures (in particular `tests/test_mcp_mount.py`, `tests/test_ingest_codebase.py`).

- [ ] **Step 7: Commit**

```bash
git add app/graph/ingest.py app/mcp_server.py tests/test_ingest_codebase.py
git commit -m "refactor(graph): extract ingest_codebase_impl into app/graph/ingest.py

Drops the unused ctx: ToolContext param -- the function never read it.
Makes ingest_codebase_impl importable with zero RAG-stack dependencies,
needed by the upcoming graph-only MCP entrypoint.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `app/graph_mcp_server.py` — standalone graph-only entrypoint

**Files:**
- Create: `app/graph_mcp_server.py`
- Test: `tests/test_graph_mcp_server.py`

**Interfaces:**
- Consumes: `app.graph.ingest.IngestCodebaseResult`, `app.graph.ingest.ingest_codebase_impl(source: str) -> IngestCodebaseResult` (from Task 1).
- Produces: `app.graph_mcp_server.create_graph_only_app() -> FastAPI`, mounting a FastMCP app at `/` (tool `ingest_codebase`) and a `GET /health` route.

- [ ] **Step 1: Write the failing test**

Create `tests/test_graph_mcp_server.py`:

```python
import json

from fastapi.testclient import TestClient

from app.graph_mcp_server import create_graph_only_app


def test_health_returns_graph_only_mode():
    app = create_graph_only_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "mode": "graph-only"}


def test_mcp_endpoint_is_mounted():
    app = create_graph_only_app()
    with TestClient(app) as client:
        resp = client.post("/mcp", json={})
        assert resp.status_code != 404


def test_ingest_codebase_tool_runs_against_fixture_repo(tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")

    from app.graph.ingest import ingest_codebase_impl

    result = ingest_codebase_impl(str(tmp_path))

    out_dir = tmp_path / "graphtr-out"
    assert result.symbol_count > 0
    assert (out_dir / "graph.json").exists()
    graph = json.loads((out_dir / "graph.json").read_text())
    assert len(graph["nodes"]) == result.symbol_count
```

(The third test exercises the same `ingest_codebase_impl` the tool wraps —
covering the tool's request/response plumbing over the real MCP transport
needs a full MCP client session, which is out of scope here; the mount
check above already confirms the route exists.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_graph_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.graph_mcp_server'`

- [ ] **Step 3: Create `app/graph_mcp_server.py`**

```python
"""Dependency-light MCP server exposing only `ingest_codebase`, for
machines that don't run (or can't install) the RAG stack: no torch,
sentence-transformers, qdrant-client, neo4j, or psycopg required. See
docs/superpowers/specs/2026-08-06-graph-only-mcp-server-design.md."""
import contextlib

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from app.graph.ingest import IngestCodebaseResult, ingest_codebase_impl


def create_graph_only_app() -> FastAPI:
    mcp = FastMCP("hoton-graphtr-graph-only", stateless_http=True, json_response=True)

    @mcp.tool()
    def ingest_codebase(source: str) -> IngestCodebaseResult:
        """Parse a local repo path into <repo>/graphtr-out/ (graph.json, manifest.json,
        graphtr.html). One-shot: nothing is kept server-side, query the output offline
        with scripts/query.py. Git URLs are not supported -- clone first, pass a path."""
        return ingest_codebase_impl(source)

    mcp_app = mcp.streamable_http_app()  # must be called once before mcp.session_manager exists

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="hoton-graphtr-graph-only", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": "graph-only"}

    # mcp_app already owns the "/mcp" path internally (FastMCP's streamable_http_path
    # default) -- mounting it at "/mcp" here would double the prefix to "/mcp/mcp".
    app.mount("/", mcp_app)

    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_graph_mcp_server.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Verify the module has no RAG-stack imports**

Run:
```bash
python3 -c "
import ast
tree = ast.parse(open('app/graph_mcp_server.py').read())
names = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        names.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        names.add(node.module)
forbidden = {'app.config', 'app.clients.embeddings', 'app.clients.llm', 'app.clients.qdrant_store', 'app.rag', 'app.agentic', 'qdrant_client', 'torch', 'transformers', 'sentence_transformers', 'neo4j', 'psycopg'}
hit = {n for n in names if any(n == f or n.startswith(f + '.') for f in forbidden)}
assert not hit, f'forbidden imports found: {hit}'
print('OK: no RAG-stack imports')
"
```
Expected: `OK: no RAG-stack imports`

- [ ] **Step 6: Commit**

```bash
git add app/graph_mcp_server.py tests/test_graph_mcp_server.py
git commit -m "feat(graph): add standalone graph-only MCP server entrypoint

app/graph_mcp_server.py exposes only ingest_codebase, importing nothing
beyond fastapi/mcp/app.graph.ingest -- runnable without torch,
transformers, sentence-transformers, qdrant-client, neo4j, or psycopg.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `requirements-graph.txt`

**Files:**
- Create: `requirements-graph.txt`

**Interfaces:**
- Consumes: nothing (static file).
- Produces: `requirements-graph.txt`, installable via `pip install -r requirements-graph.txt`, sufficient to run `uvicorn app.graph_mcp_server:create_graph_only_app --factory`.

- [ ] **Step 1: Create the file**

```
fastapi==0.139.2
uvicorn[standard]==0.32.1
mcp==1.12.4
tree-sitter-language-pack==1.13.3
```

- [ ] **Step 2: Verify it's sufficient in isolation**

Run (uses a throwaway venv so this doesn't touch the repo's dev `.venv`):
```bash
python3 -m venv /tmp/graph-only-check
/tmp/graph-only-check/bin/pip install --quiet -r requirements-graph.txt
cd /home/toannc/workplace/HoTon-Project/HoTon-GrapHTR
/tmp/graph-only-check/bin/python3 -c "
from app.graph_mcp_server import create_graph_only_app
create_graph_only_app()
print('OK: app builds with only requirements-graph.txt installed')
"
rm -rf /tmp/graph-only-check
```
Expected: `OK: app builds with only requirements-graph.txt installed` (no `ModuleNotFoundError`).

- [ ] **Step 3: Commit**

```bash
git add requirements-graph.txt
git commit -m "build: add requirements-graph.txt for the graph-only server

Trimmed dependency set (fastapi, uvicorn, mcp, tree-sitter-language-pack)
-- excludes torch/transformers/sentence-transformers/qdrant-client/neo4j/
psycopg, none of which app.graph_mcp_server imports.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `install.sh --graph-only` flag

**Files:**
- Modify: `install.sh:36-43` (arg parsing), `install.sh:132-192` (deps + model pre-download), `install.sh:236-261` (run/print commands)

**Interfaces:**
- Consumes: `requirements-graph.txt` (Task 3), `app.graph_mcp_server:create_graph_only_app` (Task 2).
- Produces: `bash install.sh --graph-only [--run]` — same clone/venv/skill-bootstrap flow as today, installing the trimmed requirements and (with `--run`) starting the graph-only server.

- [ ] **Step 1: Extend arg parsing**

In `install.sh`, replace the existing arg-parsing block:

```bash
RUN_AFTER=0
TARGET_DIR=""
for arg in "$@"; do
  case "$arg" in
    --run) RUN_AFTER=1 ;;
    *) TARGET_DIR="$arg" ;;
  esac
done
```

with:

```bash
RUN_AFTER=0
GRAPH_ONLY=0
TARGET_DIR=""
for arg in "$@"; do
  case "$arg" in
    --run) RUN_AFTER=1 ;;
    --graph-only) GRAPH_ONLY=1 ;;
    *) TARGET_DIR="$arg" ;;
  esac
done
```

- [ ] **Step 2: Branch the dependency install**

Replace:

```bash
echo "Installing dependencies (torch/transformers make first run slow, be patient)..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
```

with:

```bash
pip install --quiet --upgrade pip
if [ "$GRAPH_ONLY" -eq 1 ]; then
  echo "Installing dependencies (graph-only: no torch/transformers)..."
  pip install --quiet -r requirements-graph.txt
else
  echo "Installing dependencies (torch/transformers make first run slow, be patient)..."
  pip install --quiet -r requirements.txt
fi
```

- [ ] **Step 3: Skip `.env`/`DEPLOY_MODE` and model pre-download in graph-only mode**

Replace this exact block (currently between the dependency install and the
`# Auto-bootstrap the calling project` comment):

```bash
ENV_FILE="$REPO_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "Creating .env from docker/.env.example"
  cp "$REPO_ROOT/docker/.env.example" "$ENV_FILE"
fi

if grep -q '^DEPLOY_MODE=' "$ENV_FILE"; then
  # -i.bak works on both BSD sed (macOS) and GNU sed (Linux); plain -i does not.
  sed -i.bak 's/^DEPLOY_MODE=.*/DEPLOY_MODE=local/' "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
else
  echo "DEPLOY_MODE=local" >>"$ENV_FILE"
fi

if [ "$RUN_AFTER" -eq 1 ]; then
  # Stop any existing server before pre-downloading models below -- a
  # leftover server (from a prior --run) holds the reasoning model in GPU
  # memory, so the pipeline() load in the pre-download step can hit CUDA
  # OOM if the old process is still running. Stopping it here, rather than
  # after pre-download, also frees the local Qdrant storage lock before the
  # new server starts further down.
  if command -v lsof >/dev/null 2>&1; then
    EXISTING_PIDS=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
    if [ -n "$EXISTING_PIDS" ]; then
      echo ""
      echo "Stopping existing server on :$PORT (pid(s) $EXISTING_PIDS) to load new code..."
      kill $EXISTING_PIDS 2>/dev/null || true
      sleep 1
      STILL_RUNNING=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
      if [ -n "$STILL_RUNNING" ]; then
        kill -9 $STILL_RUNNING 2>/dev/null || true
      fi
    fi
  fi
fi

echo ""
echo "Pre-downloading embedding/reasoning models (skips any already cached)..."
"$PYTHON_BIN" <<'PYEOF' || echo "Warning: model pre-download failed, will download lazily on first request instead." >&2
from sentence_transformers import SentenceTransformer
from transformers import pipeline
import torch
from app.config import get_settings

if torch.cuda.is_available():
    print(f"GPU detected: {torch.cuda.get_device_name(0)} (CUDA {torch.version.cuda}) -- models will run on GPU.")
else:
    print("No NVIDIA GPU detected -- models will run on CPU.")

settings = get_settings()
print(f"  embed model: {settings.embed_model_name}")
SentenceTransformer(settings.embed_model_name)
print(f"  reasoning model: {settings.reasoning_model_name}")
device = 0 if torch.cuda.is_available() else -1
pipeline("text-generation", model=settings.reasoning_model_name, device=device)
print("Models ready.")
PYEOF

echo ""
echo "Setup done. .env has DEPLOY_MODE=local."
echo "Data will be written under \$LOCAL_DATA_DIR (default ./graphtr-out)."
```

with:

```bash
if [ "$GRAPH_ONLY" -eq 1 ]; then
  echo ""
  echo "Setup done (graph-only -- no .env, no models to download)."
else
  ENV_FILE="$REPO_ROOT/.env"
  if [ ! -f "$ENV_FILE" ]; then
    echo "Creating .env from docker/.env.example"
    cp "$REPO_ROOT/docker/.env.example" "$ENV_FILE"
  fi

  if grep -q '^DEPLOY_MODE=' "$ENV_FILE"; then
    # -i.bak works on both BSD sed (macOS) and GNU sed (Linux); plain -i does not.
    sed -i.bak 's/^DEPLOY_MODE=.*/DEPLOY_MODE=local/' "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
  else
    echo "DEPLOY_MODE=local" >>"$ENV_FILE"
  fi

  echo ""
  echo "Pre-downloading embedding/reasoning models (skips any already cached)..."
  "$PYTHON_BIN" <<'PYEOF' || echo "Warning: model pre-download failed, will download lazily on first request instead." >&2
from sentence_transformers import SentenceTransformer
from transformers import pipeline
import torch
from app.config import get_settings

if torch.cuda.is_available():
    print(f"GPU detected: {torch.cuda.get_device_name(0)} (CUDA {torch.version.cuda}) -- models will run on GPU.")
else:
    print("No NVIDIA GPU detected -- models will run on CPU.")

settings = get_settings()
print(f"  embed model: {settings.embed_model_name}")
SentenceTransformer(settings.embed_model_name)
print(f"  reasoning model: {settings.reasoning_model_name}")
device = 0 if torch.cuda.is_available() else -1
pipeline("text-generation", model=settings.reasoning_model_name, device=device)
print("Models ready.")
PYEOF

  echo ""
  echo "Setup done. .env has DEPLOY_MODE=local."
  echo "Data will be written under \$LOCAL_DATA_DIR (default ./graphtr-out)."
fi
```

Note: this drops the `if [ "$RUN_AFTER" -eq 1 ]; then ... lsof ... fi`
port-stop pre-flight that used to sit right before the model pre-download —
it only existed to avoid a CUDA OOM while re-loading the reasoning model,
which graph-only never does. Step 4 reinstates an unconditional (mode-
independent) version of it, since a stale server on `$PORT` needs stopping
before *any* `--run`, not just the model-loading one.

- [ ] **Step 4: Add a mode-independent port-stop-before-run check**

Insert this immediately after Step 2's dependency-install block and before
the `if [ "$GRAPH_ONLY" -eq 1 ]` block from Step 3:

```bash
if [ "$RUN_AFTER" -eq 1 ] && command -v lsof >/dev/null 2>&1; then
  EXISTING_PIDS=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
  if [ -n "$EXISTING_PIDS" ]; then
    echo ""
    echo "Stopping existing server on :$PORT (pid(s) $EXISTING_PIDS) to load new code..."
    kill $EXISTING_PIDS 2>/dev/null || true
    sleep 1
    STILL_RUNNING=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
    if [ -n "$STILL_RUNNING" ]; then
      kill -9 $STILL_RUNNING 2>/dev/null || true
    fi
  fi
fi
```

- [ ] **Step 5: Branch the run command**

Replace:

```bash
if [ "$RUN_AFTER" -eq 1 ]; then
  # The server is shared across every project that installs into ~/.graphtr,
  # but a re-run always means the repo was just git-pulled to a newer commit
  # (see the clone/pull step above) -- a healthy-but-stale server left running
  # would keep serving the old in-memory code indefinitely. Always restart so
  # --run picks up whatever just changed; other projects sharing this server
  # will see a brief reconnect. (Old server already stopped above, before
  # pre-download, to free GPU memory and the local Qdrant storage lock.)
  LOG_FILE="$REPO_ROOT/graphtr-server.log"
  echo ""
  echo "Starting server on :$PORT (detached -- survives Ctrl+C / shell exit)..."
  nohup uvicorn app.main:create_app --factory --host 0.0.0.0 --port "$PORT" \
    >"$LOG_FILE" 2>&1 </dev/null &
  SERVER_PID=$!
  disown
  echo "Server started (pid $SERVER_PID). Logs: $LOG_FILE"
  echo "Stop with: kill $SERVER_PID"
else
  echo ""
  echo "Run:"
  echo "  cd $REPO_ROOT && source .venv/bin/activate"
  echo "  uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT"
  echo ""
  echo "Verify:"
  echo "  curl http://localhost:$PORT/health"
fi
```

with:

```bash
if [ "$GRAPH_ONLY" -eq 1 ]; then
  APP_TARGET="app.graph_mcp_server:create_graph_only_app"
else
  APP_TARGET="app.main:create_app"
fi

if [ "$RUN_AFTER" -eq 1 ]; then
  # The server is shared across every project that installs into ~/.graphtr,
  # but a re-run always means the repo was just git-pulled to a newer commit
  # (see the clone/pull step above) -- a healthy-but-stale server left running
  # would keep serving the old in-memory code indefinitely. Always restart so
  # --run picks up whatever just changed; other projects sharing this server
  # will see a brief reconnect. (Old server already stopped above, before
  # pre-download, to free GPU memory and the local Qdrant storage lock.)
  LOG_FILE="$REPO_ROOT/graphtr-server.log"
  echo ""
  echo "Starting server on :$PORT (detached -- survives Ctrl+C / shell exit)..."
  nohup uvicorn "$APP_TARGET" --factory --host 0.0.0.0 --port "$PORT" \
    >"$LOG_FILE" 2>&1 </dev/null &
  SERVER_PID=$!
  disown
  echo "Server started (pid $SERVER_PID). Logs: $LOG_FILE"
  echo "Stop with: kill $SERVER_PID"
else
  echo ""
  echo "Run:"
  echo "  cd $REPO_ROOT && source .venv/bin/activate"
  echo "  uvicorn $APP_TARGET --factory --host 0.0.0.0 --port $PORT"
  echo ""
  echo "Verify:"
  echo "  curl http://localhost:$PORT/health"
fi
```

- [ ] **Step 6: Manual verification (no automated test harness for install.sh)**

Run from the repo root:
```bash
bash -n install.sh
```
Expected: no output (syntax OK).

Then a dry run against a scratch target dir:
```bash
bash install.sh --graph-only /tmp/graph-only-install-check
```
Expected: completes with `Setup done (graph-only -- no .env, no models to download).`, and:
```bash
grep -q sentence-transformers /tmp/graph-only-install-check/requirements.txt 2>/dev/null; echo $?
```
Expected: `1` (no match — `requirements.txt` full deps were never installed in this venv) — verify instead:
```bash
/tmp/graph-only-install-check/.venv/bin/pip list 2>/dev/null | grep -i -E "torch|sentence-transformers|qdrant-client|neo4j" ; echo "exit:$?"
```
Expected: `exit:1` (grep found nothing — none of those packages are installed).

Clean up:
```bash
rm -rf /tmp/graph-only-install-check
```

- [ ] **Step 7: Commit**

```bash
git add install.sh
git commit -m "feat(install): add --graph-only flag

Installs requirements-graph.txt instead of requirements.txt, skips .env/
DEPLOY_MODE setup and model pre-download, and runs
app.graph_mcp_server:create_graph_only_app instead of app.main:create_app.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Update docs

**Files:**
- Modify: `README.md` (add a short "graph-only / weak machines" subsection near the existing `install.sh` usage docs)

**Interfaces:**
- Consumes: nothing new — documents Tasks 1-4's user-facing surface (`install.sh --graph-only`).
- Produces: nothing consumed by other tasks; this is the last task.

- [ ] **Step 1: Find the current `install.sh` usage section**

Run: `grep -n "install.sh" README.md | head -20`

Read the surrounding section (the "Using graphtr in another project" /
self-hosting section referenced by `docs/connecting-a-new-repo.md`) to match
its existing tone and heading level before editing.

- [ ] **Step 2: Add the graph-only subsection**

Immediately after the existing `install.sh --run` explanation paragraph (the
one describing the zero-service `DEPLOY_MODE=local` setup), add:

```markdown
#### Graph-only (weak machines, no RAG)

If a machine can't run (or install) the embedding/reasoning models — e.g. a
low-spec laptop — but you still want it to build a code graph and expose it
to a Claude Code session via the `graphtr` skill, use:

```bash
bash install.sh --graph-only --run
```

This installs `requirements-graph.txt` (no torch/transformers/
sentence-transformers/qdrant-client/neo4j/psycopg) and starts
`app/graph_mcp_server.py`'s minimal MCP server, which exposes only
`ingest_codebase`. No `.env`, no `DEPLOY_MODE`, no model downloads. Every
other `graphtr` skill capability (RAG retrieval, memory, profile) is
unavailable on a graph-only install — use the full `install.sh --run` (or
the shared-server setup in `docs/connecting-a-new-repo.md`) for those.
```

- [ ] **Step 3: Verify the doc renders sensibly**

Run: `grep -n "Graph-only" README.md`
Expected: one match, at the heading you just added.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): document install.sh --graph-only for weak machines

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
