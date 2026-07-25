# Hybrid/Graph RAG Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fuse code-graph context into `get_rag_context`'s response when a caller supplies `repo_id`, so a single MCP call returns vector chunks + memories + code-graph context instead of requiring two separate tool calls.

**Architecture:** `get_rag_context_impl` gains an optional `repo_id` param. When present (and a `graph_store` is configured), a new small LLM-prompt function extracts 0-3 candidate code-symbol names from the query, those are BFS-searched (depth 1) against the repo's subgraph and merged/capped, and the result is rendered into a new `[Code Graph Context]` section appended by `build_full_context`. No `repo_id` → identical behavior to today (backward-compatible default).

**Tech Stack:** Python, FastMCP, Qdrant (via existing `ToolContext`/`GraphStore` abstractions), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-07-24-hybrid-graph-rag-fusion-design.md`

---

## Task 1: Query → graph keyword extraction

**Files:**
- Create: `app/agentic/graph_fusion.py`
- Test: `tests/test_graph_fusion.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph_fusion.py
from app.agentic.graph_fusion import extract_graph_keywords


class FakeLLM:
    def __init__(self, response_text):
        self._response_text = response_text
        self.last_prompt = None

    def generate(self, prompt, max_new_tokens=60, temperature=0.0):
        self.last_prompt = prompt
        return self._response_text


def test_extract_graph_keywords_parses_json_array():
    llm = FakeLLM('["retrieve_chunks", "Embedder"]')
    result = extract_graph_keywords(llm, "how does chunk retrieval work?")
    assert result == ["retrieve_chunks", "Embedder"]


def test_extract_graph_keywords_empty_array_returns_empty_list():
    llm = FakeLLM("[]")
    assert extract_graph_keywords(llm, "hi there") == []


def test_extract_graph_keywords_unparsable_text_returns_empty_list():
    llm = FakeLLM("I'm not sure what symbols are relevant here.")
    assert extract_graph_keywords(llm, "hi there") == []


def test_extract_graph_keywords_caps_at_three():
    llm = FakeLLM('["a", "b", "c", "d", "e"]')
    assert extract_graph_keywords(llm, "query") == ["a", "b", "c"]


def test_extract_graph_keywords_includes_query_in_prompt():
    llm = FakeLLM("[]")
    extract_graph_keywords(llm, "What is memory decay?")
    assert "What is memory decay?" in llm.last_prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_graph_fusion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agentic.graph_fusion'`

- [ ] **Step 3: Write the implementation**

```python
# app/agentic/graph_fusion.py
"""Reduce a natural-language query to candidate code-symbol names, since
bfs_query matches by substring against a node's `name` field, not free text."""

import json

_KEYWORD_PROMPT = """Given this question, list 0-3 code symbol, class, or function names that \
might be directly relevant to answering it, based on typical naming conventions. \
Respond as a JSON array of strings, e.g. ["retrieve_chunks", "Embedder"]. If none are relevant, respond [].

Question: {query}
JSON:"""


def extract_graph_keywords(llm, query: str) -> list[str]:
    raw = llm.generate(_KEYWORD_PROMPT.format(query=query), max_new_tokens=60, temperature=0.0)
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [k for k in parsed if isinstance(k, str) and k.strip()][:3]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_graph_fusion.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/agentic/graph_fusion.py tests/test_graph_fusion.py
git commit -m "$(cat <<'EOF'
feat(agentic): add LLM-based code-symbol keyword extraction

First piece of hybrid RAG fusion: reduces a natural-language query to
0-3 candidate symbol names so the code graph can be BFS-searched by
keyword. Fails safe to [] on unparsable LLM output.
EOF
)"
```

---

## Task 2: Merge multi-keyword BFS results into one subgraph

**Files:**
- Modify: `app/graph/graph_query.py`
- Test: `tests/test_graph_query.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph_query.py`:

```python
from app.graph.graph_query import bfs_query, explain_node, fuse_graph_context, shortest_path
```

(replace the existing `from app.graph.graph_query import bfs_query, explain_node, shortest_path` import line at the top of the file with the line above, adding `fuse_graph_context`)

```python
class _FakeGraphStore:
    def __init__(self, nodes, edges):
        self._nodes = nodes
        self._edges = edges

    def get_subgraph(self, user_id, repo_id):
        return self._nodes, self._edges


def test_fuse_graph_context_merges_multiple_keywords_dedup():
    store = _FakeGraphStore(NODES, EDGES)
    nodes, edges = fuse_graph_context(store, "u1", "r1", ["dog", "bark"], max_nodes=15)
    names = {n["name"] for n in nodes}
    assert names == {"Dog", "Animal", "bark", "helper"}


def test_fuse_graph_context_respects_max_nodes_cap():
    store = _FakeGraphStore(NODES, EDGES)
    nodes, _edges = fuse_graph_context(store, "u1", "r1", ["dog"], max_nodes=1)
    assert len(nodes) == 1


def test_fuse_graph_context_empty_keywords_returns_empty():
    store = _FakeGraphStore(NODES, EDGES)
    assert fuse_graph_context(store, "u1", "r1", []) == ([], [])


def test_fuse_graph_context_filters_edges_to_kept_nodes():
    store = _FakeGraphStore(NODES, EDGES)
    nodes, edges = fuse_graph_context(store, "u1", "r1", ["dog"], max_nodes=15)
    kept_ids = {n["id"] for n in nodes}
    assert all(e["source"] in kept_ids and e["target"] in kept_ids for e in edges)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_graph_query.py -v`
Expected: FAIL with `ImportError: cannot import name 'fuse_graph_context'`

- [ ] **Step 3: Write the implementation**

Append to `app/graph/graph_query.py`:

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
    seen_ids: set[str] = set()
    merged_nodes: list[dict] = []

    for kw in keywords:
        kw_nodes, _kw_edges = bfs_query(nodes, edges, kw, depth=1)
        for n in kw_nodes:
            if n["id"] not in seen_ids:
                seen_ids.add(n["id"])
                merged_nodes.append(n)
        if len(merged_nodes) >= max_nodes:
            break

    merged_nodes = merged_nodes[:max_nodes]
    kept_ids = {n["id"] for n in merged_nodes}
    merged_edges = [e for e in edges if e["source"] in kept_ids and e["target"] in kept_ids]
    return merged_nodes, merged_edges
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_graph_query.py -v`
Expected: PASS (all tests in file, including the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add app/graph/graph_query.py tests/test_graph_query.py
git commit -m "$(cat <<'EOF'
feat(graph): add fuse_graph_context for multi-keyword subgraph merge

Merges depth-1 bfs_query results across several keywords into one
deduped subgraph, capped at max_nodes to avoid context-window bloat.
Second piece of hybrid RAG fusion.
EOF
)"
```

---

## Task 3: Render graph context into the RAG context text

**Files:**
- Modify: `app/rag/context.py`
- Test: `tests/test_context.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context.py`:

```python
def test_build_graph_context_section_empty_returns_empty_string():
    from app.rag.context import build_graph_context_section

    assert build_graph_context_section([], []) == ""


def test_build_graph_context_section_renders_nodes_and_edges():
    from app.rag.context import build_graph_context_section

    nodes = [
        {"id": "1", "name": "retrieve_chunks", "kind": "function", "file_path": "app/rag/retrieval.py", "start_line": 29},
        {"id": "2", "name": "embed_single", "kind": "method"},
    ]
    edges = [{"source": "1", "target": "2", "type": "CALLS"}]

    section = build_graph_context_section(nodes, edges)

    assert "[Code Graph Context]" in section
    assert "retrieve_chunks (function) — app/rag/retrieval.py:29" in section
    assert "--CALLS--> embed_single" in section


def test_build_full_context_appends_graph_section_after_rag_context():
    from app.rag.context import build_full_context
    from app.rag.profile import UserProfile

    chunks = [RetrievedChunk(id="c1", content="Chunk body", document_title="Doc A", source_url=None, similarity=0.9, document_expired=False)]
    graph_nodes = [{"id": "1", "name": "foo", "kind": "function"}]

    result = build_full_context(chunks, [], UserProfile(), graph_nodes=graph_nodes, graph_edges=[])

    assert "[Code Graph Context]" in result
    assert result.index("[Knowledge Context]") < result.index("[Code Graph Context]")


def test_build_full_context_no_graph_args_unchanged():
    from app.rag.context import build_full_context
    from app.rag.profile import UserProfile

    assert build_full_context([], [], UserProfile()) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_graph_context_section'`

- [ ] **Step 3: Write the implementation**

In `app/rag/context.py`, add after `build_profile_context_section` (before `build_full_context`):

```python
def build_graph_context_section(graph_nodes: list[dict], graph_edges: list[dict]) -> str:
    if not graph_nodes:
        return ""

    name_by_id = {n["id"]: n["name"] for n in graph_nodes}
    edges_by_source: dict[str, list[dict]] = {}
    for e in graph_edges:
        edges_by_source.setdefault(e["source"], []).append(e)

    sections = ["[Code Graph Context]", "─" * 30]
    for n in graph_nodes:
        kind = f" ({n['kind']})" if n.get("kind") else ""
        location = f" — {n['file_path']}:{n['start_line']}" if n.get("file_path") and n.get("start_line") is not None else ""
        sections.append(f"{n['name']}{kind}{location}")
        for e in edges_by_source.get(n["id"], []):
            target_name = name_by_id.get(e["target"], e["target"])
            sections.append(f"  --{e['type']}--> {target_name}")

    sections.append("─" * 30)
    return "\n".join(sections)
```

Then replace the existing `build_full_context` function:

```python
def build_full_context(
    chunks: list[RetrievedChunk],
    memories: list[RetrievedMemory],
    profile: UserProfile,
    graph_nodes: list[dict] | None = None,
    graph_edges: list[dict] | None = None,
) -> str:
    parts = []
    profile_section = build_profile_context_section(profile)
    if profile_section:
        parts.append(profile_section)
    if memories:
        parts.append(build_memory_context_section(memories))
    if chunks:
        parts.append(build_rag_context_section(chunks))
    graph_section = build_graph_context_section(graph_nodes or [], graph_edges or [])
    if graph_section:
        parts.append(graph_section)
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_context.py -v`
Expected: PASS (all tests in file, including the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add app/rag/context.py tests/test_context.py
git commit -m "$(cat <<'EOF'
feat(rag): render code-graph nodes/edges into RAG context text

Adds build_graph_context_section and an optional graph_nodes/
graph_edges param on build_full_context, appended as a fourth section
after chunks. Backward-compatible: omitting the new params reproduces
today's output exactly. Third piece of hybrid RAG fusion.
EOF
)"
```

---

## Task 4: Wire fusion into `get_rag_context`

**Files:**
- Modify: `app/mcp_server.py:11-24` (imports), `app/mcp_server.py:147-182` (`get_rag_context_impl`), `app/mcp_server.py:272-276` (tool wrapper)
- Test: `tests/test_mcp_tools.py` (modify `_ctx`/`ScriptedLLM`, append 3 tests)

- [ ] **Step 1: Write the failing tests**

In `tests/test_mcp_tools.py`, replace the `ScriptedLLM` class with (adds `graph_keywords` param + routing branch):

```python
class ScriptedLLM:
    """Routes canned responses by inspecting distinguishing text in each prompt,
    since the full pipeline calls generate() for routing, HyDE, grading, graph
    keyword extraction, and continue-decisions in sequence with different prompts."""

    def __init__(self, *, classification="single", hyde_text="hypothetical passage", grade="0.9", continue_json='{"enough": true, "next_query": null}', extraction="[]", graph_keywords="[]"):
        self.classification = classification
        self.hyde_text = hyde_text
        self.grade = grade
        self.continue_json = continue_json
        self.extraction = extraction
        self.graph_keywords = graph_keywords

    def generate(self, prompt, max_new_tokens=256, temperature=0.1):
        if "Classification:" in prompt:
            return self.classification
        if "Passage:" in prompt and "hypothetical" in prompt.lower():
            return self.hyde_text
        if "Relevance score:" in prompt:
            return self.grade
        if "code symbol, class, or function names" in prompt:
            return self.graph_keywords
        if "JSON:" in prompt and "enough" in prompt:
            return self.continue_json
        return self.extraction
```

Replace the `_ctx` helper (adds `graph_store` passthrough):

```python
def _ctx(qdrant, graph_store=None, **llm_kwargs):
    llm = ScriptedLLM(**llm_kwargs) if llm_kwargs else FakeLLM()
    return build_tool_context(qdrant, FakeEmbedder(), llm, _fake_web_search, graph_store=graph_store)
```

Append these tests (they use the `graph_store` fixture from `tests/conftest.py`, so the test file needs no new import for it — pytest fixtures are auto-discovered):

```python
@pytest.mark.asyncio
async def test_get_rag_context_fuses_graph_when_repo_id_given(qdrant, graph_store):
    user_id = uuid.uuid4()
    graph_store.upsert_symbols([
        {"id": "1", "user_id": str(user_id), "repo_id": "r1", "kind": "function", "name": "retrieve_chunks",
         "file_path": "app/rag/retrieval.py", "start_line": 29, "end_line": 66, "language": "python"},
    ])
    ctx = _ctx(qdrant, graph_store=graph_store, classification="single", grade="0.9", graph_keywords='["retrieve_chunks"]')

    result = await get_rag_context_impl(ctx, str(user_id), "how does chunk retrieval work?", repo_id="r1")

    assert "[Code Graph Context]" in result.context_text
    assert "retrieve_chunks (function)" in result.context_text


@pytest.mark.asyncio
async def test_get_rag_context_skips_graph_fusion_without_repo_id(qdrant, graph_store):
    user_id = uuid.uuid4()
    graph_store.upsert_symbols([
        {"id": "1", "user_id": str(user_id), "repo_id": "r1", "kind": "function", "name": "retrieve_chunks"},
    ])
    ctx = _ctx(qdrant, graph_store=graph_store, classification="single", grade="0.9", graph_keywords='["retrieve_chunks"]')

    result = await get_rag_context_impl(ctx, str(user_id), "how does chunk retrieval work?")

    assert "[Code Graph Context]" not in result.context_text


@pytest.mark.asyncio
async def test_get_rag_context_graph_fusion_skipped_when_no_keywords_extracted(qdrant, graph_store):
    user_id = uuid.uuid4()
    graph_store.upsert_symbols([
        {"id": "1", "user_id": str(user_id), "repo_id": "r1", "kind": "function", "name": "retrieve_chunks"},
    ])
    ctx = _ctx(qdrant, graph_store=graph_store, classification="single", grade="0.9")  # graph_keywords defaults to "[]"

    result = await get_rag_context_impl(ctx, str(user_id), "query", repo_id="r1")

    assert "[Code Graph Context]" not in result.context_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_tools.py -v`
Expected: FAIL — `test_get_rag_context_fuses_graph_when_repo_id_given` and
`test_get_rag_context_graph_fusion_skipped_when_no_keywords_extracted` fail with
`TypeError: get_rag_context_impl() got an unexpected keyword argument 'repo_id'`.
(`test_get_rag_context_skips_graph_fusion_without_repo_id` passes already since it
doesn't pass `repo_id` — that's expected, it's a regression guard.)

- [ ] **Step 3: Write the implementation**

In `app/mcp_server.py`, update the import block (lines 11-22) to add the two new imports:

```python
from app.rag.context import apply_self_consistency, build_full_context
from app.agentic.grading import crag_correct
from app.agentic.graph_fusion import extract_graph_keywords
from app.agentic.hyde import generate_hypothetical_answer
from app.rag.memory import extract_and_store_memories, retrieve_memories
from app.rag.profile import get_or_create_profile, update_profile_from_message
from app.agentic.react import run_multi_step_retrieval
from app.rag.retrieval import retrieve_chunks
from app.agentic.routing import QueryComplexity, classify_query
from app.graph.code_graph_store import GraphStore
from app.graph.graph_query import bfs_query, explain_node, fuse_graph_context, shortest_path
from app.graph.repo_source import resolve_repo_source
from app.graph.repo_watcher import RepoWatcherManager
```

Replace `get_rag_context_impl` (currently lines 147-182):

```python
async def get_rag_context_impl(
    ctx: ToolContext, user_id: str, query: str, repo_id: str | None = None
) -> RagContextResult:
    # This handler is `async def`, so anything called directly (not via
    # asyncio.to_thread) blocks the single event loop for its full duration —
    # LLM inference and Qdrant network search are neither. Offloading each
    # blocking step to a thread lets concurrent requests interleave instead of
    # fully serializing behind one caller's multi-step retrieval chain.
    uid = uuid.UUID(user_id)
    complexity = await asyncio.to_thread(classify_query, ctx.llm, query)

    if complexity == QueryComplexity.DIRECT:
        return RagContextResult(context_text="", chunks_used=0, memories_used=0, chunks=[])

    if complexity == QueryComplexity.MULTI:
        chunks, memories = await asyncio.to_thread(
            run_multi_step_retrieval, ctx.client, ctx.embedder, ctx.llm, uid, query, RAG_TOP_K, RAG_MIN_SIMILARITY
        )
        chunks = await crag_correct(ctx.llm, ctx.web_search_fn, query, chunks)
    else:  # SINGLE
        hyde_query = await asyncio.to_thread(generate_hypothetical_answer, ctx.llm, query)
        chunks = await asyncio.to_thread(
            retrieve_chunks, ctx.client, ctx.embedder, uid, hyde_query, RAG_TOP_K, RAG_MIN_SIMILARITY
        )
        chunks = await crag_correct(ctx.llm, ctx.web_search_fn, query, chunks)
        memories = await asyncio.to_thread(
            retrieve_memories, ctx.client, ctx.embedder, uid, query, MEMORY_TOP_K, MEMORY_MIN_SIMILARITY
        )

    apply_self_consistency(memories, query)
    profile = await asyncio.to_thread(get_or_create_profile, ctx.client, uid)

    graph_nodes: list[dict] = []
    graph_edges: list[dict] = []
    if repo_id and ctx.graph_store:
        keywords = await asyncio.to_thread(extract_graph_keywords, ctx.llm, query)
        if keywords:
            graph_nodes, graph_edges = fuse_graph_context(ctx.graph_store, user_id, repo_id, keywords)

    context_text = build_full_context(chunks, memories, profile, graph_nodes, graph_edges)
    return RagContextResult(
        context_text=context_text,
        chunks_used=len(chunks),
        memories_used=len(memories),
        chunks=_to_chunk_out(chunks),
    )
```

Replace the `get_rag_context` tool wrapper inside `build_mcp_server` (currently lines 272-276):

```python
    @mcp.tool()
    async def get_rag_context(user_id: str, query: str, repo_id: str | None = None) -> RagContextResult:
        """Retrieve merged profile/memory/knowledge context for a chat turn.
        Pass repo_id to also fuse relevant code-graph context into the result."""
        with track_usage(ctx.usage_store, "get_rag_context", user_id):
            return await get_rag_context_impl(ctx, user_id, query, repo_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_tools.py -v`
Expected: PASS (all tests, including the 3 new ones)

- [ ] **Step 5: Run the full test suite (regression check)**

Run: `pytest`
Expected: PASS, 0 failures — confirms no existing caller of `get_rag_context_impl`/`get_rag_context` broke from the new optional param.

- [ ] **Step 6: Commit**

```bash
git add app/mcp_server.py tests/test_mcp_tools.py
git commit -m "$(cat <<'EOF'
feat(mcp): fuse code-graph context into get_rag_context via repo_id

get_rag_context now accepts an optional repo_id. When given (and a
graph_store is configured), the query is reduced to candidate code
symbols, BFS-searched against the repo's subgraph, and rendered into
a [Code Graph Context] section alongside chunks/memories. Omitting
repo_id reproduces prior behavior exactly (regression-tested).

Closes the Hybrid/Graph RAG gap from
docs/superpowers/specs/2026-07-24-hybrid-graph-rag-fusion-design.md.
EOF
)"
```

---

## Self-Review Notes (completed during planning)

- **Spec coverage:** interface change (Task 4 §1), trigger condition (Task 4 §2/§3), keyword extraction (Task 1), fetch/merge (Task 2), rendering (Task 3), wiring (Task 4), error handling (fail-safe empty-list/skip paths tested in Tasks 1, 2, 4), testing (unit tests per task + integration + full-suite regression in Task 4). No spec section without a corresponding task.
- **Placeholder scan:** none found — every step has runnable code and exact commands.
- **Type consistency:** `extract_graph_keywords(llm, query) -> list[str]` (Task 1) is the exact signature consumed in Task 4. `fuse_graph_context(graph_store, user_id, repo_id, keywords, max_nodes=15) -> tuple[list[dict], list[dict]]` (Task 2) matches its call site in Task 4. `build_graph_context_section(graph_nodes, graph_edges) -> str` and `build_full_context(..., graph_nodes=None, graph_edges=None)` (Task 3) match the Task 4 call `build_full_context(chunks, memories, profile, graph_nodes, graph_edges)`.
