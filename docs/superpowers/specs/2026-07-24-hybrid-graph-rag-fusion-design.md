# Hybrid/Graph RAG Fusion — Design

## Context

Gap analysis of `ragdoc/rag.md` and `ragdoc/8_rag_architectures.md` against the
current codebase found the app implements Naive/HyDE/Corrective/Adaptive RAG
and a rich memory-validation pipeline (confidence decay, contrastive updates,
claim classification, TTL pruning) closely matching rag.md's design. The code
graph (`app/graph/`) exists but is exposed only as a standalone `query_code_graph`
MCP tool — it is never fused with vector-chunk retrieval into a single answer.
That makes it "Graph RAG" in isolation, not "Hybrid RAG" as described in
`8_rag_architectures.md` §6 (parallel Vector DB + Graph DB context feeding one
prompt).

This spec covers closing that one gap: fusing code-graph context into
`get_rag_context_impl`'s existing context assembly.

## Goal

When a caller supplies a `repo_id` alongside `user_id`/`query`, the RAG context
returned by `get_rag_context` should include relevant code-graph nodes/edges
in addition to vector chunks and memories — one unified context, not two
separate tools the caller has to call and merge themselves.

## Non-goals

- No CRAG-style relevance grading applied to graph nodes.
- No new MCP tool — this extends the existing `get_rag_context` tool.
- No change to `query_code_graph`'s standalone behavior/signature.
- No cross-repo or cross-user graph search.

## Design

### 1. Interface change (backward-compatible)

`get_rag_context_impl(ctx, user_id, query)` gains one new optional parameter:

```python
async def get_rag_context_impl(ctx: ToolContext, user_id: str, query: str, repo_id: str | None = None) -> RagContextResult:
```

Same addition propagates to the MCP tool wrapper in `build_mcp_server`
(`app/mcp_server.py`). Callers that omit `repo_id` see identical behavior to
today (text-only context) — the parameter defaults to `None` and fusion is
skipped entirely, so no existing caller breaks.

### 2. Trigger condition

Fusion runs whenever `repo_id is not None and ctx.graph_store is not None`.
No new query-complexity classification branch — this is orthogonal to the
existing DIRECT/SINGLE/MULTI routing in `agentic/routing.py` and runs
regardless of which complexity branch produced the chunks/memories.

### 3. Query → graph keywords

`bfs_query` (in `app/graph/graph_query.py`) seeds on substring match against a
node's `name` field, so a natural-language query needs to be reduced to
candidate symbol/class/function names first.

New function `extract_graph_keywords(llm, query) -> list[str]` in
`app/agentic/` (new module `graph_fusion.py` or added to `routing.py` —
implementation's call), following the existing small-prompt pattern used by
`classify_query` / `grade_relevance`:

```
Prompt: "Given this question, list 0-3 code symbol/class/function names that
might be directly relevant. Respond as a JSON array of strings. If none, []."
```

Parse failure or empty result → `[]` (fails safe: skip graph fusion, don't
error the whole request — same fail-safe pattern already used throughout
`agentic/`).

### 4. Fetching and merging the subgraph

New function in `app/graph/graph_query.py` (or a thin wrapper in
`mcp_server.py` next to `_to_node_out`):

```python
def fuse_graph_context(graph_store, user_id, repo_id, keywords: list[str], max_nodes: int = 15) -> tuple[list[dict], list[dict]]:
    nodes, edges = graph_store.get_subgraph(user_id, repo_id)
    seen_ids: set[str] = set()
    merged_nodes: list[dict] = []
    for kw in keywords:
        kw_nodes, kw_edges = bfs_query(nodes, edges, kw, depth=1)
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

`get_subgraph` is called once regardless of keyword count. Depth fixed at 1
hop (seed + direct neighbors) to keep the context window contribution small,
per rag.md's context-pollution guidance — this is not user-configurable at
this stage. `max_nodes=15` caps total size; keywords are processed in the
order the LLM returned them, so the first (presumably most relevant) keyword's
matches are kept preferentially when the cap is hit.

### 5. Rendering into context text

New `build_graph_context_section(nodes, edges) -> str` in `app/rag/context.py`,
matching the shape of the existing `build_rag_context_section`:

```
[Code Graph Context]
──────────────────────────────
retrieve_chunks (function) — app/rag/retrieval.py:29
  --CALLS--> embed_single
  --CALLS--> query_points
...
```

Empty `nodes` → returns `""` (same convention as the other section builders).

`build_full_context` gains a 4th optional section, appended after the RAG
chunk section:

```python
def build_full_context(chunks, memories, profile, graph_nodes=None, graph_edges=None) -> str:
    ...
    graph_section = build_graph_context_section(graph_nodes or [], graph_edges or [])
    if graph_section:
        parts.append(graph_section)
    ...
```

### 6. Wiring in `get_rag_context_impl`

Runs after existing chunk/memory retrieval and CRAG correction, before
`build_full_context`:

```python
if repo_id and ctx.graph_store:
    keywords = await asyncio.to_thread(extract_graph_keywords, ctx.llm, query)
    graph_nodes, graph_edges = ctx.graph_store and fuse_graph_context(ctx.graph_store, user_id, repo_id, keywords) or ([], [])
else:
    graph_nodes, graph_edges = [], []
```

(LLM call offloaded via `asyncio.to_thread` — same reasoning as the other
blocking calls in this handler: it's a synchronous `llm.generate()` call in an
`async def` handler.)

### 7. Error handling

No new failure modes:
- `ctx.graph_store is None` (local mode without a graph, or graph feature
  unused) → skip, identical to existing `None` checks elsewhere in
  `mcp_server.py`.
- Unparsable LLM keyword output → `[]`, already the established fail-safe
  pattern in `agentic/routing.py` and `agentic/grading.py`.
- `repo_id` referring to a repo with no ingested graph → `get_subgraph`
  returns empty lists (existing behavior), `fuse_graph_context` then returns
  `([], [])`, section renders as `""`.

### 8. Testing

Unit:
- `extract_graph_keywords`: valid JSON parses to list; garbage/empty → `[]`.
- `fuse_graph_context`: multi-keyword union dedupes by node id; respects
  `max_nodes` cap; edges filtered to only those between kept nodes.
- `build_graph_context_section`: empty input → `""`; non-empty renders
  expected format.

Integration:
- Extend existing `get_rag_context` tests with a case passing `repo_id` and a
  fake `graph_store` (mirroring `FakeGraphStore` in `tests/conftest.py`),
  asserting the graph section appears in `context_text`.
- Existing tests that call `get_rag_context` without `repo_id` must continue
  passing unchanged (regression check for backward compatibility).

## Open follow-up (not in this spec)

Gap #2 (Multimodal RAG) is a separate, independent spec — to be brainstormed
next per user's stated sequencing.
