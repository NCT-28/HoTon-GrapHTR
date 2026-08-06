# Stateless Code-Graph Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `ingest_codebase` into a single stateless MCP call that parses a local repo and writes `graphtr-out/{graph.json,manifest.json,graphtr.html}` into that repo, and delete every piece of server-resident code-graph state it used to need.

**Architecture:** Ingest becomes parse → write files → render viewer → return counts. Everything that existed to keep a code graph resident (the repo watcher, the code-graph half of `GraphStore`, code-symbol Qdrant vectors, `query_code_graph`/`export_graph_snapshot`, the graph-RAG fusion branch of `get_rag_context`, the dashboard's project panel) is removed, in dependency order: callers first, store last. Offline querying already exists in `scripts/query.py` and `graphtr.html` and is untouched.

**Tech Stack:** Python 3, FastAPI, FastMCP (`mcp.server.fastmcp`), pydantic, tree-sitter (`app/graph/code_parser.py`), Qdrant, Neo4j, SQLite, pytest.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-08-06-stateless-code-graph-ingest-design.md`. Every task traces to a section of it.
- Both deploy modes must keep working for what remains (`DEPLOY_MODE=server` → Neo4j/Qdrant/Postgres; `DEPLOY_MODE=local` → files under `Settings.local_data_dir`). The code graph is the one concern that stops being deploy-mode-dispatched.
- Tests construct `create_app()` / `build_tool_context()` with fakes — never monkeypatch the real factories. Follow `tests/conftest.py`.
- Run the full suite with `pytest` from the repo root. `scripts/test_*.py` are collected too and import their subject bare (`from build_viewer import ...`); do not add `scripts/__init__.py`.
- Match surrounding style: no docstring/comment churn on lines you aren't changing.
- Three breaking MCP signature changes ship together and are intended:
  `ingest_codebase(source)`, `get_rag_context(user_id, query)`, and the removal of `query_code_graph` / `export_graph_snapshot`.

## File Structure

**Created**
- `app/graph/snapshot_writer.py` — serializes parsed symbols/edges to `graphtr-out/graph.json`, merge-writes `manifest.json`, and renders `graphtr.html` by loading `scripts/build_viewer.py`. Sole owner of the on-disk snapshot format.
- `tests/test_snapshot_writer.py`
- `tests/test_ingest_codebase.py`

**Modified**
- `scripts/build_viewer.py` — extract `build(out_dir)`; drop the dead "Code vectors" row.
- `app/mcp_server.py` — rewrite `ingest_codebase_impl`; delete `query_code_graph`/`export_graph_snapshot`; delete the fusion branch of `get_rag_context_impl`; drop `watcher_manager` from `ToolContext`.
- `app/main.py` — drop watcher wiring.
- `app/rag/context.py` — drop `build_graph_context_section` + the graph params of `build_full_context`.
- `app/dashboard/queries.py`, `app/dashboard/router.py`, `app/dashboard/templates/dashboard.html`, `app/dashboard/tracker.py` — drop the project panel, the code-symbol collection row, and the two dead tool names.
- `app/clients/qdrant_store.py` — drop `count_code_symbol_embeddings`.
- `app/graph/code_graph_store.py` — drop the code-graph surface from the ABC + both remaining impls; delete `LocalMultiRepoGraphStore`.
- `tests/conftest.py` — trim `FakeGraphStore`.
- Docs: `.claude/skills/graphtr/SKILL.md`, `CLAUDE.md`.

**Deleted**
- `app/graph/repo_watcher.py`, `app/graph/graph_query.py`, `app/agentic/graph_fusion.py`
- `tests/test_repo_watcher.py`, `tests/test_main_watcher_wiring.py`, `tests/test_mcp_graph_tools.py`, `tests/test_graph_query.py`, `tests/test_graph_fusion.py`, `tests/test_code_graph_store.py`, `tests/test_code_graph_store_integration.py`, `tests/test_sqlite_graph_store.py`, `tests/test_local_multi_repo_graph_store.py`

---

### Task 1: `build_viewer.build(out_dir)`

Spec §4 and §1 ("no `code_symbol_count`"). Pure refactor plus one dead UI row removed; no other task depends on the row change, but `build()` is what Task 2 imports.

**Files:**
- Modify: `scripts/build_viewer.py:112-138` (`main`), `scripts/build_viewer.py:400-403` (template overview rows)
- Test: `scripts/test_build_viewer.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `build(out_dir: Path) -> None` in `scripts/build_viewer.py`. Reads `out_dir/graph.json` (required) and `out_dir/manifest.json` (optional), writes `out_dir/graphtr.html`, prints a one-line summary. `main()` keeps its existing hand-rolled `--out-dir` parsing.

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_build_viewer.py`:

```python
def test_build_is_callable_without_argv(tmp_path):
    from build_viewer import build

    (tmp_path / "graph.json").write_text(json.dumps({"nodes": NODES, "edges": EDGES}))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "repo_id": "r1", "last_indexed_at": "2026-08-06T00:00:00",
    }))

    build(tmp_path)

    html = (tmp_path / "graphtr.html").read_text()
    assert "r1" in html
    assert "2026-08-06T00:00:00" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/test_build_viewer.py::test_build_is_callable_without_argv -v`
Expected: FAIL with `ImportError: cannot import name 'build' from 'build_viewer'`

- [ ] **Step 3: Extract `build(out_dir)`**

Replace `scripts/build_viewer.py`'s `main()` (lines 112-138) with:

```python
def build(out_dir: Path) -> None:
    graph_path = out_dir / "graph.json"
    manifest_path = out_dir / "manifest.json"
    output_path = out_dir / "graphtr.html"

    graph = json.loads(graph_path.read_text())
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    raw_nodes, raw_edges, legend, edge_legend = build_data(graph["nodes"], graph["edges"])

    def js_json(obj) -> str:
        # Escape "</" so embedded strings can't prematurely close the <script> tag.
        return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")

    html = (
        TEMPLATE
        .replace("__RAW_NODES__", js_json(raw_nodes))
        .replace("__RAW_EDGES__", js_json(raw_edges))
        .replace("__LEGEND__", js_json(legend))
        .replace("__EDGE_LEGEND__", js_json(edge_legend))
        .replace("__MANIFEST__", js_json(manifest))
    )
    output_path.write_text(html)
    print(f"wrote {output_path} ({len(raw_nodes)} nodes, {len(raw_edges)} edges)")


def main():
    args = sys.argv[1:]
    out_dir = DEFAULT_OUT_DIR
    if len(args) >= 2 and args[0] == "--out-dir":
        out_dir = Path(args[1])
    build(out_dir)
```

Do **not** introduce `argparse` — the CLI's arg handling is out of scope.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/test_build_viewer.py -v`
Expected: PASS (all tests in the file, including the pre-existing `main()` ones)

- [ ] **Step 5: Remove the dead "Code vectors" row**

Code symbols are no longer embedded (spec §3), so `manifest.code_symbol_count` will never be written again and the row would render `—` forever.

In `scripts/build_viewer.py`, delete this line from `TEMPLATE` (line 402):

```
  <div class="overview-row"><span class="overview-lbl">Code vectors</span><span class="overview-val">${MANIFEST.code_symbol_count == null ? '—' : MANIFEST.code_symbol_count}</span></div>
```

and update the comment above it (line 400) from
`// (code_symbol_count/last_indexed_at) -- a static snapshot, no server needed to view it.`
to
`// (last_indexed_at) -- a static snapshot, no server needed to view it.`

- [ ] **Step 6: Update the test that asserted on that row**

In `scripts/test_build_viewer.py`, rename
`test_main_bakes_manifest_code_symbol_count_and_last_indexed_at_into_html` to
`test_main_bakes_manifest_last_indexed_at_into_html`, drop `"code_symbol_count": 42` from the
manifest it writes, and drop any assertion on `42` / "Code vectors". Keep the `last_indexed_at`
and `repo_id` assertions.

- [ ] **Step 7: Run the scripts tests**

Run: `pytest scripts/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add scripts/build_viewer.py scripts/test_build_viewer.py
git commit -m "refactor(build_viewer): extract callable build(out_dir)

Lets the MCP ingest path render graphtr.html without shelling out. Also
drops the Code vectors overview row: code symbols are no longer embedded,
so manifest.code_symbol_count is never written."
```

---

### Task 2: `app/graph/snapshot_writer.py`

Spec §1 (`write_graph_snapshot`, manifest merge). This is the whole on-disk format in one file.

**Files:**
- Create: `app/graph/snapshot_writer.py`
- Test: `tests/test_snapshot_writer.py`

**Interfaces:**
- Consumes: `build(out_dir: Path) -> None` (Task 1). `ParsedSymbol` / `ParsedEdge` from `app/graph/code_parser.py` — `ParsedSymbol` has `id, kind, name, file_path, start_line, end_line, language, content_hash`; `ParsedEdge` has `source, target, type`.
- Produces:
  - `write_graph_snapshot(local_path: str, repo_id: str, symbols: list, edges: list) -> str` — writes `<local_path>/graphtr-out/graph.json` and merge-updates `manifest.json`; returns the `graphtr-out` dir path.
  - `render_viewer(out_dir: str) -> None` — renders `graphtr.html` in that dir.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_snapshot_writer.py`:

```python
import json
import os

from app.graph.code_parser import ParsedEdge, ParsedSymbol
from app.graph.snapshot_writer import render_viewer, write_graph_snapshot

SYMBOLS = [
    ParsedSymbol(id="s1", kind="function", name="foo", file_path="a.py",
                 start_line=1, end_line=3, language="python", content_hash="h1"),
    ParsedSymbol(id="s2", kind="class", name="Bar", file_path="b.py",
                 start_line=1, end_line=9, language="python", content_hash="h2"),
]
EDGES = [ParsedEdge(source="s1", target="s2", type="CALLS")]


def test_write_graph_snapshot_writes_nodes_and_edges(tmp_path):
    out_dir = write_graph_snapshot(str(tmp_path), "r1", SYMBOLS, EDGES)

    graph = json.loads((tmp_path / "graphtr-out" / "graph.json").read_text())
    assert out_dir == str(tmp_path / "graphtr-out")
    assert [n["id"] for n in graph["nodes"]] == ["s1", "s2"]
    assert graph["nodes"][0] == {
        "id": "s1", "name": "foo", "kind": "function", "file_path": "a.py",
        "start_line": 1, "end_line": 3, "language": "python",
    }
    assert graph["edges"] == [{"source": "s1", "target": "s2", "type": "CALLS"}]


def test_write_graph_snapshot_tallies_kinds_and_types_into_manifest(tmp_path):
    write_graph_snapshot(str(tmp_path), "r1", SYMBOLS, EDGES)

    manifest = json.loads((tmp_path / "graphtr-out" / "manifest.json").read_text())
    assert manifest["repo_id"] == "r1"
    assert manifest["node_count"] == 2
    assert manifest["edge_count"] == 1
    assert manifest["node_kinds"] == {"function": 1, "class": 1}
    assert manifest["edge_types"] == {"CALLS": 1}
    assert manifest["last_indexed_at"]
    assert manifest["exported_at"]
    # code symbols are no longer embedded, so this stat is gone entirely
    assert "code_symbol_count" not in manifest


def test_write_graph_snapshot_preserves_foreign_manifest_keys(tmp_path):
    # scripts/index_knowledge.py mints rag_user_id into this same file; a blind
    # overwrite would orphan every knowledge doc indexed under it.
    out_dir = tmp_path / "graphtr-out"
    os.makedirs(out_dir)
    (out_dir / "manifest.json").write_text(json.dumps({"rag_user_id": "keep-me", "node_count": 999}))

    write_graph_snapshot(str(tmp_path), "r1", SYMBOLS, EDGES)

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["rag_user_id"] == "keep-me"
    assert manifest["node_count"] == 2


def test_render_viewer_writes_html_from_graph_json(tmp_path):
    out_dir = write_graph_snapshot(str(tmp_path), "r1", SYMBOLS, EDGES)

    render_viewer(out_dir)

    html = (tmp_path / "graphtr-out" / "graphtr.html").read_text()
    assert "foo" in html
    assert "r1" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_snapshot_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.graph.snapshot_writer'`

- [ ] **Step 3: Write the implementation**

Create `app/graph/snapshot_writer.py`:

```python
"""Writes a parsed repo's code graph to <repo>/graphtr-out/ as the final
artifact: graph.json + manifest.json + graphtr.html. This is the whole
persistence story for the code graph -- nothing is kept server-side."""

import datetime
import importlib.util
import json
import os
from pathlib import Path

# scripts/ is deliberately not a package (its files are copied standalone into
# other projects by init_graphtr_skills.py, and scripts/test_*.py import their
# subject bare), so the renderer is loaded by path rather than imported.
_BUILD_VIEWER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_viewer.py"

# Keys this writer owns in manifest.json. Anything else in the file -- notably
# scripts/index_knowledge.py's rag_user_id -- is preserved on write.
_OWNED_MANIFEST_KEYS = (
    "repo_id", "node_count", "edge_count", "node_kinds", "edge_types",
    "last_indexed_at", "exported_at",
)


def _load_build():
    spec = importlib.util.spec_from_file_location("graphtr_build_viewer", _BUILD_VIEWER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build


def write_graph_snapshot(local_path: str, repo_id: str, symbols: list, edges: list) -> str:
    out_dir = os.path.join(local_path, "graphtr-out")
    os.makedirs(out_dir, exist_ok=True)

    nodes = [
        {"id": s.id, "name": s.name, "kind": s.kind, "file_path": s.file_path,
         "start_line": s.start_line, "end_line": s.end_line, "language": s.language}
        for s in symbols
    ]
    edge_dicts = [{"source": e.source, "target": e.target, "type": e.type} for e in edges]

    node_kinds: dict[str, int] = {}
    for s in symbols:
        kind = s.kind or "unknown"
        node_kinds[kind] = node_kinds.get(kind, 0) + 1

    edge_types: dict[str, int] = {}
    for e in edges:
        edge_types[e.type] = edge_types.get(e.type, 0) + 1

    with open(os.path.join(out_dir, "graph.json"), "w") as f:
        json.dump({"nodes": nodes, "edges": edge_dicts}, f)

    now = datetime.datetime.utcnow().isoformat()
    manifest_path = os.path.join(out_dir, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError):
            manifest = {}
    manifest.update({
        "repo_id": repo_id,
        "node_count": len(nodes),
        "edge_count": len(edge_dicts),
        "node_kinds": node_kinds,
        "edge_types": edge_types,
        "last_indexed_at": now,
        "exported_at": now,
    })
    manifest.pop("code_symbol_count", None)  # stale: code symbols are no longer embedded
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)

    return out_dir


def render_viewer(out_dir: str) -> None:
    _load_build()(Path(out_dir))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_snapshot_writer.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/graph/snapshot_writer.py tests/test_snapshot_writer.py
git commit -m "feat(graph): add snapshot_writer for graphtr-out/ artifacts

Serializes parsed symbols/edges straight to graph.json + manifest.json and
renders graphtr.html, with no GraphStore round trip. manifest.json is
merge-written so index_knowledge.py's rag_user_id survives a re-ingest."
```

---

### Task 3: Rewrite `ingest_codebase`, delete `query_code_graph` / `export_graph_snapshot`

Spec §1, §2. After this task the two removed tools are gone from the MCP surface and ingest no longer touches `GraphStore` or the watcher.

**Files:**
- Modify: `app/mcp_server.py:224-234` (`ingest_codebase_impl`), `:237-288` (the two `_impl`s to delete), `:325-346` (tool registrations), `:20-22` (imports), `:120-133` (`QueryCodeGraphResult`/`GraphSnapshotResult`)
- Modify: `app/dashboard/tracker.py:17-25` (`MCP_TOOL_NAMES`)
- Test: `tests/test_ingest_codebase.py` (create), `tests/test_mcp_tools_usage_tracking.py` (rewrite)

**Interfaces:**
- Consumes: `write_graph_snapshot(local_path, repo_id, symbols, edges) -> str` and `render_viewer(out_dir) -> None` (Task 2); `parse_repo(repo_id: str, root_path: str) -> tuple[list[ParsedSymbol], list[ParsedEdge]]`; `resolve_repo_source(source: str, repo_id: str) -> str`.
- Produces: `ingest_codebase_impl(ctx: ToolContext, source: str) -> IngestCodebaseResult` and the MCP tool `ingest_codebase(source: str)`. `IngestCodebaseResult` keeps its three fields (`repo_id`, `symbol_count`, `edge_count`); `symbol_count` now means parsed symbols, not stored nodes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingest_codebase.py`:

```python
import json

import pytest

from app.mcp_server import IngestCodebaseResult, ToolContext, ingest_codebase_impl


def _ctx():
    return ToolContext(client=None, embedder=None, llm=None, web_search_fn=None)


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

    result = ingest_codebase_impl(_ctx(), str(repo))

    out_dir = repo / "graphtr-out"
    assert isinstance(result, IngestCodebaseResult)
    assert (out_dir / "graph.json").exists()
    assert (out_dir / "manifest.json").exists()
    assert (out_dir / "graphtr.html").exists()


def test_ingest_codebase_result_counts_match_graph_json(tmp_path):
    repo = _fixture_repo(tmp_path)

    result = ingest_codebase_impl(_ctx(), str(repo))

    graph = json.loads((repo / "graphtr-out" / "graph.json").read_text())
    assert result.symbol_count == len(graph["nodes"])
    assert result.edge_count == len(graph["edges"])
    assert result.symbol_count > 0


def test_ingest_codebase_mints_a_fresh_repo_id_every_call(tmp_path):
    repo = _fixture_repo(tmp_path)

    first = ingest_codebase_impl(_ctx(), str(repo))
    second = ingest_codebase_impl(_ctx(), str(repo))

    assert first.repo_id != second.repo_id


def test_ingest_codebase_rejects_git_urls(tmp_path):
    with pytest.raises(ValueError, match="git URLs are not supported"):
        ingest_codebase_impl(_ctx(), "https://github.com/example/repo.git")


def test_ingest_codebase_preserves_rag_user_id_in_manifest(tmp_path):
    repo = _fixture_repo(tmp_path)
    out_dir = repo / "graphtr-out"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(json.dumps({"rag_user_id": "keep-me"}))

    ingest_codebase_impl(_ctx(), str(repo))

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["rag_user_id"] == "keep-me"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest_codebase.py -v`
Expected: FAIL — `ingest_codebase_impl()` still requires a `user_id` positional arg (`TypeError`)

- [ ] **Step 3: Rewrite `ingest_codebase_impl`**

Replace `app/mcp_server.py:224-234` with:

```python
def ingest_codebase_impl(ctx: ToolContext, source: str) -> IngestCodebaseResult:
    # Git URLs would clone into settings.code_repos_dir inside the container and
    # the graphtr-out/ written there would be unreachable to the caller -- and with
    # a fresh repo_id per call, every clone would leak a new directory.
    if source.startswith(("http://", "https://")):
        raise ValueError("git URLs are not supported; clone the repo and pass a local path")

    repo_id = str(uuid.uuid4())
    local_path = resolve_repo_source(source, repo_id)
    symbols, edges = parse_repo(repo_id, local_path)
    out_dir = write_graph_snapshot(local_path, repo_id, symbols, edges)
    render_viewer(out_dir)
    return IngestCodebaseResult(repo_id=repo_id, symbol_count=len(symbols), edge_count=len(edges))
```

- [ ] **Step 4: Delete the other two `_impl`s and fix imports**

In `app/mcp_server.py`:

- Delete `query_code_graph_impl` (lines 237-261) and `export_graph_snapshot_impl` (lines 264-288).
- Delete the `QueryCodeGraphResult` and `GraphSnapshotResult` model classes. **Keep** `GraphNodeOut`, `GraphEdgeOut`, `_to_node_out` for now — Task 4 removes them once nothing else references them.
- Delete import line 20 (`from app.clients.qdrant_store import count_code_symbol_embeddings`).
- Narrow import line 22 to `from app.graph.graph_query import fuse_graph_context` —
  `bfs_query`/`shortest_path`/`explain_node` lose their last caller here, but
  `get_rag_context_impl:188` still calls `fuse_graph_context` until Task 4 removes it.
  Deleting the whole line now breaks `get_rag_context`.
- Add `from app.graph.code_parser import parse_repo` and
  `from app.graph.snapshot_writer import render_viewer, write_graph_snapshot`.

- [ ] **Step 5: Update the tool registrations**

In `build_mcp_server`, replace the `ingest_codebase` registration (lines 325-329) with:

```python
    @mcp.tool()
    def ingest_codebase(source: str) -> IngestCodebaseResult:
        """Parse a local repo path into <repo>/graphtr-out/ (graph.json, manifest.json,
        graphtr.html). One-shot: nothing is kept server-side, query the output offline
        with scripts/query.py. Git URLs are not supported -- clone first, pass a path."""
        with track_usage(ctx.usage_store, "ingest_codebase", ""):
            return ingest_codebase_impl(ctx, source)
```

Delete the `query_code_graph` (331-339) and `export_graph_snapshot` (341-346) registrations entirely.

- [ ] **Step 6: Drop the dead tool names from `MCP_TOOL_NAMES`**

In `app/dashboard/tracker.py:17-25`, delete the `"query_code_graph",` and
`"export_graph_snapshot",` entries. The frozenset keeps the other six names.

- [ ] **Step 7: Rewrite `tests/test_mcp_tools_usage_tracking.py`**

Its only test drove `query_code_graph_impl`. Replace the whole file with:

```python
from app.mcp_server import ToolContext, ingest_codebase_impl
from app.dashboard.tracker import track_usage


def test_ingest_codebase_impl_records_usage(tmp_path, usage_store):
    (tmp_path / "mod.py").write_text("def helper():\n    return 1\n")
    ctx = ToolContext(client=None, embedder=None, llm=None, web_search_fn=None)

    with track_usage(usage_store, "ingest_codebase", ""):
        ingest_codebase_impl(ctx, str(tmp_path))

    assert usage_store.events[0]["tool_name"] == "ingest_codebase"
    assert usage_store.events[0]["user_id"] == ""
    assert usage_store.events[0]["success"] is True
```

- [ ] **Step 8: Delete the obsolete graph-tool test file**

```bash
git rm tests/test_mcp_graph_tools.py
```

It tests `query_code_graph_impl` / `export_graph_snapshot_impl` and asserts on
`ctx.watcher_manager.watched_repos()` — all three are gone.

- [ ] **Step 9: Run the tests**

Run: `pytest tests/test_ingest_codebase.py tests/test_mcp_tools_usage_tracking.py tests/test_tracker.py -v`
Expected: PASS

Note: `tests/test_tracker.py:24` passes the literal string `"query_code_graph"` to
`track_usage`, which is fine — `track_usage` doesn't validate against `MCP_TOOL_NAMES`.
Leave it; Task 6 revisits it only if `MCP_TOOL_NAMES` filtering makes it fail.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat(mcp): make ingest_codebase a one-shot stateless call

Parses a local repo and writes graphtr-out/{graph.json,manifest.json,
graphtr.html} directly, with no GraphStore or watcher involvement. Drops
user_id (no DB to scope by) and rejects git URLs (the in-container clone's
output is unreachable to the caller). query_code_graph and
export_graph_snapshot are removed -- scripts/query.py and graphtr.html
already cover both offline."
```

---

### Task 4: Remove the graph-RAG fusion path

Spec §6 / Accepted regression 2. `get_rag_context` stops enriching with code-graph context; every module that existed only for that path goes with it.

**Files:**
- Modify: `app/mcp_server.py:151-200` (`get_rag_context_impl`), `:294-299` (tool registration), imports, `GraphNodeOut`/`GraphEdgeOut`/`_to_node_out`
- Modify: `app/rag/context.py:86-126`
- Delete: `app/agentic/graph_fusion.py`, `app/graph/graph_query.py`, `tests/test_graph_fusion.py`, `tests/test_graph_query.py`
- Test: `tests/test_mcp_tools.py`, `tests/test_context.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `get_rag_context_impl(ctx: ToolContext, user_id: str, query: str) -> RagContextResult` (no `repo_id`); `build_full_context(chunks, memories, profile) -> str` (no graph params).

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp_tools.py`, replace the three fusion tests
(`test_get_rag_context_fuses_graph_when_repo_id_given`,
`test_get_rag_context_skips_graph_fusion_without_repo_id`,
`test_get_rag_context_graph_fusion_skipped_when_no_keywords_extracted`, lines ~161-205)
with this single test:

```python
@pytest.mark.asyncio
async def test_get_rag_context_takes_no_repo_id(qdrant):
    import inspect

    from app.mcp_server import get_rag_context_impl

    params = list(inspect.signature(get_rag_context_impl).parameters)
    assert params == ["ctx", "user_id", "query"]

    ctx = _ctx(qdrant, classification="single", grade="0.9")
    result = await get_rag_context_impl(ctx, str(uuid.uuid4()), "how does chunk retrieval work?")

    assert "[Code Graph Context]" not in result.context_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_tools.py::test_get_rag_context_takes_no_repo_id -v`
Expected: FAIL — `assert ['ctx', 'user_id', 'query', 'repo_id'] == ['ctx', 'user_id', 'query']`

- [ ] **Step 3: Strip the fusion branch from `mcp_server.py`**

Change the signature at line 151-153 to:

```python
async def get_rag_context_impl(ctx: ToolContext, user_id: str, query: str) -> RagContextResult:
```

Delete lines 183-188 (the `graph_nodes` / `graph_edges` locals and the
`if repo_id and ctx.graph_store:` block) and change line 190 to:

```python
    context_text = build_full_context(chunks, memories, profile)
```

Delete import line 13 (`from app.agentic.graph_fusion import extract_graph_keywords`) and the
`from app.graph.graph_query import fuse_graph_context` line Task 3 left behind.

Update the tool registration (lines 294-299) to:

```python
    @mcp.tool()
    async def get_rag_context(user_id: str, query: str) -> RagContextResult:
        """Retrieve merged profile/memory/knowledge context for a chat turn."""
        with track_usage(ctx.usage_store, "get_rag_context", user_id):
            return await get_rag_context_impl(ctx, user_id, query)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mcp_tools.py -v`
Expected: PASS

- [ ] **Step 5: Delete the orphaned modules**

```bash
git rm app/agentic/graph_fusion.py app/graph/graph_query.py tests/test_graph_fusion.py tests/test_graph_query.py
```

`graph_query.py` had exactly two consumers — `query_code_graph_impl` (deleted in Task 3)
and `fuse_graph_context` (deleted here). The offline equivalents live in `scripts/query.py`.

- [ ] **Step 6: Drop the graph parameters from `build_full_context`**

In `app/rag/context.py`, delete `build_graph_context_section` (lines 86-105) and rewrite
`build_full_context` (lines 108-126) as:

```python
def build_full_context(
    chunks: list[RetrievedChunk],
    memories: list[RetrievedMemory],
    profile: UserProfile,
) -> str:
    parts = []
    profile_section = build_profile_context_section(profile)
    if profile_section:
        parts.append(profile_section)
    if memories:
        parts.append(build_memory_context_section(memories))
    if chunks:
        parts.append(build_rag_context_section(chunks))
    return "\n\n".join(parts)
```

- [ ] **Step 7: Update `tests/test_context.py`**

Delete the `build_graph_context_section` test (the one asserting
`"retrieve_chunks (function) — app/rag/retrieval.py:29"`) and
`test_build_full_context_appends_graph_section_after_rag_context` (lines ~107-118), plus the
`build_graph_context_section` import at the top of the file. Keep
`test_build_full_context_no_graph_args_unchanged` but rename it to
`test_build_full_context_empty_inputs_returns_empty_string`.

- [ ] **Step 8: Remove the now-unused fake knob**

In `tests/test_mcp_tools.py`'s `ScriptedLLM` (lines 38-63): delete the `graph_keywords="[]"`
constructor kwarg, the `self.graph_keywords = graph_keywords` assignment, and the
`if "code symbol, class, or function names" in prompt:` branch. Update the class docstring
to drop "graph keyword extraction" from the list.

- [ ] **Step 9: Delete the leftover graph DTOs if unreferenced**

Run: `grep -n 'GraphNodeOut\|GraphEdgeOut\|_to_node_out' app/ tests/ -r`
If the only hits are their own definitions in `app/mcp_server.py`, delete `GraphNodeOut`,
`GraphEdgeOut`, and `_to_node_out` from `app/mcp_server.py` (Task 2's `snapshot_writer`
builds plain dicts, so nothing needs them). If anything else references them, leave them
and note it.

- [ ] **Step 10: Run the suite**

Run: `pytest tests/test_mcp_tools.py tests/test_context.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor(rag): remove code-graph fusion from get_rag_context

Fusion read GraphStore.get_subgraph, which a stateless code graph no longer
populates -- keeping it would silently return nothing forever. Removes the
repo_id arg, graph_fusion.py, graph_query.py, and build_graph_context_section
along with it. Offline BFS/path/explain remain in scripts/query.py."
```

---

### Task 5: Remove `RepoWatcherManager`

Spec §3. Nothing calls it after Task 3.

**Files:**
- Delete: `app/graph/repo_watcher.py`, `tests/test_repo_watcher.py`, `tests/test_main_watcher_wiring.py`
- Modify: `app/main.py:19,30-58,62-71`, `app/mcp_server.py:24,29-48`

**Interfaces:**
- Consumes: nothing.
- Produces: `create_app(qdrant_client=None, embedder=None, browser_client=None, llm=None, web_search_fn=None, graph_store=None, usage_store=None) -> FastAPI` and `build_tool_context(client, embedder, llm, web_search_fn, graph_store=None, usage_store=None) -> ToolContext` — both without `watcher_manager`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_mount.py`:

```python
def test_tool_context_has_no_watcher_manager():
    import dataclasses

    from app.mcp_server import ToolContext

    fields = {f.name for f in dataclasses.fields(ToolContext)}
    assert "watcher_manager" not in fields
    assert {"client", "embedder", "llm", "web_search_fn", "graph_store", "usage_store"} <= fields
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_mount.py::test_tool_context_has_no_watcher_manager -v`
Expected: FAIL with `assert 'watcher_manager' not in {...}`

- [ ] **Step 3: Strip the watcher from `mcp_server.py`**

Delete import line 24 (`from app.graph.repo_watcher import RepoWatcherManager`), then rewrite
lines 29-48 as:

```python
@dataclass
class ToolContext:
    client: QdrantClient
    embedder: object
    llm: object
    web_search_fn: object  # Callable[[str], Awaitable[list[str]]]
    graph_store: GraphStore | None = None
    usage_store: UsageStore | None = None


def build_tool_context(
    client: QdrantClient, embedder, llm, web_search_fn,
    graph_store: GraphStore | None = None, usage_store: UsageStore | None = None,
) -> ToolContext:
    return ToolContext(
        client=client, embedder=embedder, llm=llm, web_search_fn=web_search_fn,
        graph_store=graph_store, usage_store=usage_store,
    )
```

- [ ] **Step 4: Strip the watcher from `main.py`**

- Delete import line 19 (`from app.graph.repo_watcher import RepoWatcherManager`).
- Change `get_repo_qdrant_client` off the line-18 import (it was only used to build the
  watcher's resolver): line 18 becomes
  `from app.clients.qdrant_store import RAG_DOCUMENTS, USER_MEMORIES, get_qdrant_client`.
- Drop `watcher_manager=None` from the `create_app` signature (line 32).
- Delete the `use_repo_resolver` block and `resolved_watcher_manager` assignment (lines 42-52),
  including their explanatory comment.
- Change the `build_tool_context` call (lines 55-58) to:

```python
    tool_ctx = build_tool_context(
        get_client_fn(), get_embedder_fn(), get_llm_fn(), resolved_web_search_fn,
        resolved_graph_store, get_usage_store_fn(),
    )
```

- Delete `resolved_watcher_manager.resume_all()` (line 65) and `resolved_watcher_manager.stop()`
  (line 71) from `lifespan`.

- [ ] **Step 5: Delete the watcher and its tests**

```bash
git rm app/graph/repo_watcher.py tests/test_repo_watcher.py tests/test_main_watcher_wiring.py
```

- [ ] **Step 6: Verify no caller survived**

Run: `grep -rn 'watcher_manager\|RepoWatcherManager\|repo_watcher' app/ tests/ scripts/`
Expected: only the prose mention in `app/graph/code_parser.py:18` ("which incremental reindex
(repo_watcher.py) relies on"). Update that comment to drop the parenthetical file reference,
since the file no longer exists.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_mcp_mount.py tests/test_status_api.py tests/test_health.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(graph): remove RepoWatcherManager

Its only job was keeping GraphStore + code-symbol Qdrant vectors in sync with
disk across the server's lifetime. With ingest writing files and returning,
watch()/reindex() and the embed/re-embed calls have no caller left."
```

---

### Task 6: Dashboard — drop the project panel and code-symbol row

Spec §5. `queries.py` was the third consumer of the code-graph `GraphStore` surface; it has to go before Task 7 can delete those methods.

**Files:**
- Modify: `app/dashboard/queries.py:1-62`, `app/dashboard/router.py:65,68`, `app/dashboard/templates/dashboard.html:100-101,253`, `app/clients/qdrant_store.py:85-112`
- Test: `tests/test_dashboard_queries.py`, `tests/test_dashboard_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `storage_breakdown(client) -> list[dict]` (no `graph_store` param, five collections). `project_breakdown` and `count_code_symbol_embeddings` no longer exist.

- [ ] **Step 1: Write the failing test**

In `tests/test_dashboard_queries.py`, replace `test_storage_breakdown_counts_all_six_collections`
with:

```python
def test_storage_breakdown_counts_all_five_collections(qdrant):
    rows = queries.storage_breakdown(qdrant)
    names = {r["collection"] for r in rows}
    assert names == {
        "rag_documents", "rag_chunks", "user_memories",
        "user_profiles", "profile_snapshots",
    }
    assert all(r["points"] == 0 for r in rows)  # fresh in-memory qdrant
    assert all(r["percent"] == 0.0 for r in rows)  # 0 / 0 total must not raise ZeroDivisionError


def test_project_breakdown_is_gone():
    assert not hasattr(queries, "project_breakdown")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_queries.py -k "five_collections or project_breakdown_is_gone" -v`
Expected: FAIL — the set comparison still finds `code_symbol_embeddings`, and
`queries.project_breakdown` still exists

- [ ] **Step 3: Rewrite `app/dashboard/queries.py`**

Replace lines 1-62 with:

```python
"""Read-side aggregation for GET /api/dashboard/summary: Qdrant storage sizes
and usage_events breakdowns by tool/user."""

from datetime import datetime, timedelta, timezone

from app.clients.qdrant_store import (
    PROFILE_SNAPSHOTS, RAG_CHUNKS, RAG_DOCUMENTS, USER_MEMORIES, USER_PROFILES,
)
from app.dashboard.tracker import MCP_TOOL_NAMES

_COLLECTIONS = [RAG_DOCUMENTS, RAG_CHUNKS, USER_MEMORIES, USER_PROFILES, PROFILE_SNAPSHOTS]


def storage_breakdown(client) -> list[dict]:
    result = []
    for name in _COLLECTIONS:
        try:
            points = client.count(collection_name=name).count
        except Exception:
            points = None
        result.append({"collection": name, "points": points})

    total = sum(r["points"] or 0 for r in result)
    for r in result:
        r["percent"] = round((r["points"] or 0) / total * 100, 1) if total else 0.0
    return result
```

`get_settings` is no longer imported here — the local/server fan-out it gated is gone.
Everything from `mcp_tool_usage` down stays untouched.

- [ ] **Step 4: Update the router**

In `app/dashboard/router.py`, change line 65 to `"storage": queries.storage_breakdown(client),`
and delete line 68 (`"by_project": queries.project_breakdown(graph_store),`). `graph_store` is
still fetched on line 55 for `health.check_neo4j` — leave that.

- [ ] **Step 5: Update the dashboard template**

In `app/dashboard/templates/dashboard.html`, four edits:

- Line 65 — delete the table column entirely:
```html
  <div class="col"><b style="font-size:12px">Neo4j graph (by project)</b><table id="by-project"></table></div>
```
  and drop the now-single-column `<div class="row">` wrapper on line 63 only if it leaves an
  empty row; otherwise leave line 64's Qdrant column as the row's sole child.
- Lines 100-101 — delete:
```javascript
  const totalNodes = data.by_project.reduce((s, p) => s + (p.node_count || 0), 0);
  const totalEdges = data.by_project.reduce((s, p) => s + (p.edge_count || 0), 0);
```
- Line 108 — delete the stat tile that consumed them:
```javascript
    { label: 'Code graph', value: fmtCompact(totalNodes), sub: `${fmtCompact(totalEdges)} edges`, accent: '#7aa2f7' },
```
- Lines 253-256 — delete the render call:
```javascript
  renderTable('by-project', data.by_project, [
    { key: 'repo_id', label: 'repo' }, { key: 'node_count', label: 'nodes' },
    { key: 'edge_count', label: 'edges' }, { key: 'last_indexed_at', label: 'last ingest' },
  ], 'no projects ingested yet');
```

- [ ] **Step 6: Delete `count_code_symbol_embeddings`**

In `app/clients/qdrant_store.py`, delete the whole `count_code_symbol_embeddings` function
(lines 85-112). Keep `CODE_SYMBOL_EMBEDDINGS`, its `bootstrap_collections` branch,
`symbol_point_id`, and `get_repo_qdrant_client` — the collection is still created, just never
written.

- [ ] **Step 7: Prune the dashboard tests**

In `tests/test_dashboard_queries.py`, delete:
- `_upsert_code_symbol_points` and every test using it
  (`test_storage_breakdown_sums_per_repo_local_qdrant_clients...`,
  `test_storage_breakdown_skips_repos_whose_local_path_no_longer_exists`,
  `test_storage_breakdown_uses_central_client_count_in_server_deploy_mode`)
- `test_project_breakdown_empty_when_no_repos`,
  `test_project_breakdown_reports_node_and_edge_counts`,
  `test_project_breakdown_does_not_materialize_the_graph`

In `tests/test_dashboard_api.py`, update `test_summary_endpoint_returns_all_six_sections`:
rename it to `test_summary_endpoint_returns_all_five_sections`, change the key assertion to
`assert set(body.keys()) == {"health", "storage", "mcp_tool_usage", "route_usage", "by_user"}`
and the count assertion to `assert len(body["storage"]) == 5`.

- [ ] **Step 8: Run the tests**

Run: `pytest tests/test_dashboard_queries.py tests/test_dashboard_api.py tests/test_health.py tests/test_qdrant_store.py -v`
Expected: PASS. `test_qdrant_store.py` is in the list because Step 6 deleted a function from
that module; its tests only assert the collection is *created*, which still holds.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor(dashboard): drop project panel and code-symbol storage row

Both were built on the GraphStore repo registry (list_repos/count_subgraph)
and on code-symbol vectors, neither of which a stateless code graph produces.
count_code_symbol_embeddings goes with them."
```

---

### Task 7: Strip the code-graph surface from `GraphStore`

Spec §7. Every caller is gone by now, so this is deletion plus keeping the TextEntity half intact for RAG.

**Files:**
- Modify: `app/graph/code_graph_store.py` (ABC at 24-103, `Neo4jGraphStore` from 106, `SqliteGraphStore` from 433, `get_graph_store` at 988-997)
- Delete: `LocalMultiRepoGraphStore` (857-985), `tests/test_code_graph_store.py`, `tests/test_code_graph_store_integration.py`, `tests/test_sqlite_graph_store.py`, `tests/test_local_multi_repo_graph_store.py`
- Modify: `tests/conftest.py:18-133`, `tests/test_entity_linker.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `GraphStore` with exactly seven methods — `upsert_text_entities`, `upsert_related_edges`, `upsert_mentions_edges`, `list_text_entities`, `list_code_symbols`, `delete_text_entities_by_source_doc`, `ping`. `get_graph_store()` returns `SqliteGraphStore(<local_data_dir>/graph.sqlite)` in local mode, `Neo4jGraphStore` in server mode.

**Why `LocalMultiRepoGraphStore` goes away entirely** (spec §7): it exists *only* to route each
repo's code symbols to its own `graphtr-out/graph.sqlite`. With the code surface gone, every
remaining method is a one-line delegation to `self._central` — identical to `SqliteGraphStore`
with extra indirection. Delete the class and point `get_graph_store()` at `SqliteGraphStore`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_graph_store_abc_exposes_only_the_text_entity_surface():
    from app.graph.code_graph_store import GraphStore

    abstract = set(GraphStore.__abstractmethods__)
    assert abstract == {
        "upsert_text_entities", "upsert_related_edges", "upsert_mentions_edges",
        "list_text_entities", "list_code_symbols", "delete_text_entities_by_source_doc",
        "ping",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_graph_store_abc_exposes_only_the_text_entity_surface -v`
Expected: FAIL — the set still contains `upsert_repo`, `get_subgraph`, etc.

- [ ] **Step 3: Trim the ABC**

In `app/graph/code_graph_store.py`, delete these abstract methods from `GraphStore` (lines 24-103):
`upsert_repo`, `upsert_symbols`, `upsert_code_edges`, `delete_repo`, `replace_repo_graph`,
`replace_files_in_repo`, `get_repo`, `list_repos`, `get_subgraph`, `list_symbol_index`,
`list_symbol_ids`, `count_subgraph`, `count_mentioning_text_entities`,
`get_mentioning_text_entities`.

- [ ] **Step 4: Trim `Neo4jGraphStore`**

Delete the concrete implementations of the same fourteen method names from `Neo4jGraphStore`
(starting at line 106), plus any private helper left with no caller inside the class.

- [ ] **Step 5: Trim `SqliteGraphStore`**

Delete the same fourteen from `SqliteGraphStore` (starting at line 433), plus their
`_*_unlocked` private helpers (`_upsert_repo_unlocked`, `_upsert_symbols_unlocked`,
`_upsert_code_edges_unlocked`, and any sibling that only those methods called).

Leave `_init_schema`'s `repos` / `code_symbols` / `code_edges` table DDL in place: the tables
become unused but dropping them changes on-disk schema for existing installs, which is out of
scope. `list_code_symbols` keeps reading `code_symbols` and now always returns `[]`
(Accepted regression 1).

- [ ] **Step 6: Delete `LocalMultiRepoGraphStore` and repoint the factory**

Delete the whole class (lines 857-985) and rewrite `get_graph_store` (lines 988-997) as:

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

Remove any import at the top of the file (e.g. `threading`) left unused by the deletion — check
with `grep -n 'threading' app/graph/code_graph_store.py` before removing.

- [ ] **Step 7: Delete the store test files**

```bash
git rm tests/test_code_graph_store.py tests/test_code_graph_store_integration.py \
       tests/test_sqlite_graph_store.py tests/test_local_multi_repo_graph_store.py
```

- [ ] **Step 8: Trim `FakeGraphStore`**

In `tests/conftest.py`, delete `upsert_repo`, `upsert_symbols`, `upsert_code_edges`,
`delete_repo`, `replace_repo_graph`, `replace_files_in_repo`, `get_repo`, `list_repos`,
`get_subgraph`, `list_symbol_index`, `count_subgraph` (lines 27-102), and the now-unused
`repos`, `symbols`, `code_edges` dataclass fields. The class keeps:

```python
@dataclass
class FakeGraphStore(GraphStore):
    text_entities: dict = field(default_factory=dict)
    related_edges: list = field(default_factory=list)
    mentions_edges: list = field(default_factory=list)

    def ping(self) -> bool:
        return True

    def upsert_text_entities(self, entities: list[dict]) -> None:
        for e in entities:
            self.text_entities[e["id"]] = e

    def upsert_related_edges(self, edges: list[dict]) -> None:
        self.related_edges.extend(edges)

    def upsert_mentions_edges(self, edges: list[dict]) -> None:
        self.mentions_edges.extend(edges)

    def list_text_entities(self, user_id: str) -> list[dict]:
        return [e for e in self.text_entities.values() if e["user_id"] == user_id]

    def list_code_symbols(self, user_id: str) -> list[dict]:
        # Code symbols are never stored anymore -- ingest_codebase writes graphtr-out/
        # instead. Kept so entity_linker's call site still resolves.
        return []

    def delete_text_entities_by_source_doc(self, user_id: str, source_doc_id: str) -> None:
        remove_ids = {
            eid for eid, e in self.text_entities.items()
            if e["user_id"] == user_id and e.get("source_doc_id") == source_doc_id
        }
        for eid in remove_ids:
            del self.text_entities[eid]
        self.related_edges = [
            e for e in self.related_edges if e["source"] not in remove_ids and e["target"] not in remove_ids
        ]
        self.mentions_edges = [e for e in self.mentions_edges if e["source"] not in remove_ids]
```

- [ ] **Step 9: Rewrite `tests/test_entity_linker.py`**

Its three tests seed symbols via `upsert_symbols`. With `list_code_symbols` permanently empty,
linking is a no-op — assert that instead. Replace the three test functions (keep `_FakeEmbedder`
and `_FakeLLM` as-is) with:

```python
def test_link_entities_to_code_is_a_no_op_without_stored_code_symbols(graph_store):
    # ingest_codebase writes graphtr-out/ instead of storing code symbols, so
    # list_code_symbols is always empty and entity->code linking never fires.
    graph_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-1", "source_memory_id": None},
    ])

    linked = link_entities_to_code(graph_store, _FakeLLM({"Retriever"}), _FakeEmbedder(), "u1", "doc-1")

    assert linked == 0
    assert graph_store.mentions_edges == []


def test_link_entities_to_code_returns_zero_when_no_entities_for_the_document(graph_store):
    graph_store.upsert_text_entities([
        {"id": "e1", "user_id": "u1", "name": "Retriever", "entity_type": "concept",
         "source_doc_id": "doc-other", "source_memory_id": None},
    ])

    linked = link_entities_to_code(graph_store, _FakeLLM({"Retriever"}), _FakeEmbedder(), "u1", "doc-1")

    assert linked == 0
```

- [ ] **Step 10: Verify nothing references the removed surface**

Run:
```bash
grep -rn 'get_subgraph\|list_repos\|count_subgraph\|upsert_symbols\|upsert_repo\|upsert_code_edges\|replace_repo_graph\|replace_files_in_repo\|list_symbol_index\|list_symbol_ids\|count_mentioning_text_entities\|get_mentioning_text_entities\|LocalMultiRepoGraphStore' app/ tests/ scripts/
```
Expected: no output. If `tests/test_graph_pipeline.py` or `tests/test_entity_extraction.py`
appear, fix those call sites the same way (they should only use TextEntity methods).

- [ ] **Step 11: Run the full suite**

Run: `pytest`
Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "refactor(graph): drop the code-graph surface from GraphStore

Every caller is gone: ingest writes files, query/export are removed, the
watcher is deleted, fusion is removed, and the dashboard no longer reads the
repo registry. LocalMultiRepoGraphStore existed only to route per-repo code
stores, so local mode now uses SqliteGraphStore directly. The TextEntity
surface RAG depends on is untouched; list_code_symbols stays and returns []."
```

---

### Task 8: Docs

Spec §10. Docs currently describe a four-step bootstrap and a watcher-based refresh that no longer exist.

**Files:**
- Modify: `.claude/skills/graphtr/SKILL.md` (lines 27, 43, 50-80, 82-112, 132-134, 136-147)
- Modify: `CLAUDE.md` (architecture table, `app/graph/` module list, Commands section if it mentions the watcher)

**Interfaces:**
- Consumes: the final `ingest_codebase(source)` signature from Task 3.
- Produces: nothing code-facing.

- [ ] **Step 1: Rewrite the SKILL.md Bootstrap section**

Replace the four numbered steps (Ingest → Export → Write → Build viewer, lines ~52-80) with one:

```markdown
1. **Ingest**: `mcp__hoton-graphtr__ingest_codebase(source="<repo path>")` → parses the repo and
   writes `graphtr-out/graph.json`, `graphtr-out/manifest.json` and `graphtr-out/graphtr.html`
   into that repo. Returns `repo_id`, `symbol_count`, `edge_count`. One call, nothing else needed.
```

- [ ] **Step 2: Rewrite the Refresh section**

Replace the whole Refresh section (lines ~82-112) with:

```markdown
## Refresh — code changed since last ingest

Re-run `mcp__hoton-graphtr__ingest_codebase(source="<repo path>")`. It reparses from scratch and
overwrites `graphtr-out/`. There is no watcher and no incremental reindex; every call mints a
fresh `repo_id`, which is expected — nothing dedupes against it.

To regenerate only the viewer after hand-editing `graph.json`:
`python3 scripts/build_viewer.py --out-dir graphtr-out`.
```

- [ ] **Step 3: Fix the remaining SKILL.md references**

- Line 27 (the update-mode table row): drop the "check Neo4j for leftover duplicate `repo_id`s"
  guidance — nothing is written to Neo4j anymore.
- Line 43: delete the `mcp__hoton-graphtr__query_code_graph` fallback paragraph.
  `scripts/query.py` is the only query path.
- Lines ~73-75: drop `code_symbol_count` and `user_id` from the documented `manifest.json` keys.
- Line 132-134 (quick-reference table): keep the manifest/viewer rows, drop the `user_id` row.
- Lines 136-147 (Common mistakes): delete the "calling `ingest_codebase` again to refresh" and
  "reusing the wrong `repo_id`" bullets. Keep the host-path-vs-bind-mount bullet. Add:
  "Passing a git URL — not supported; clone the repo and pass its path."

- [ ] **Step 4: Update CLAUDE.md**

- Architecture table, "Code graph" row: `server` column → `n/a (stateless — ingest writes to the
  repo's own graphtr-out/)`; `local` column → same; Dispatch point → `app/graph/snapshot_writer.py`.
  Keep the `code_graph_store.py` reference only where the table describes the TextEntity surface.
- `app/graph/` module list: drop `graph_query.py` and `repo_watcher.py`, add
  `snapshot_writer.py` ("writes graph.json/manifest.json/graphtr.html into the target repo").
- The `graphtr` section's Refresh bullet: replace "Refreshing the graph after code changes is a
  re-export from hoton-graphtr, not a local rebuild… do not call `ingest_codebase` again" with
  "Refresh by re-running `ingest_codebase(source)`; every call is a full reparse."

- [ ] **Step 5: Verify no doc claims a removed tool exists**

Run: `grep -rn 'query_code_graph\|export_graph_snapshot\|watcher' CLAUDE.md README.md .claude/skills/graphtr/SKILL.md`
Expected: no output (hits under `docs/superpowers/plans/` and `docs/superpowers/specs/` are
historical records — leave them).

- [ ] **Step 6: Final full-suite run**

Run: `pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "docs: rewrite graphtr skill + CLAUDE.md for stateless ingest

Bootstrap is one ingest_codebase(source) call; refresh is re-running it.
Drops the export/build steps, the watcher-based refresh flow, the
query_code_graph fallback, and the duplicate-repo_id warnings."
```

---

## Manual Verification

Automated tests don't cover the running server. Do this once before calling it done (spec §Verification):

1. `DEPLOY_MODE=local uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030`
2. Call `ingest_codebase(source="<path to a small repo>")` from an MCP client → confirm
   `graphtr-out/{graph.json,manifest.json,graphtr.html}` appear in that repo and the returned
   counts match `graph.json`.
3. If that repo already had a `manifest.json` with `rag_user_id`, confirm it survived.
4. Open `graphtr-out/graphtr.html` → confirm the overview panel renders without a "Code vectors"
   row and the graph draws.
5. `curl -u $DASHBOARD_USER:$DASHBOARD_PASSWORD localhost:8030/api/dashboard/summary` → 200, five
   sections, no `by_project`, no `code_symbol_embeddings` row.
6. Call `get_rag_context(user_id, query)` against an indexed doc set → chunks come back, no
   `[Code Graph Context]` section, no `repo_id` argument accepted.
