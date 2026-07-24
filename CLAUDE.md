# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
Codex will review your output once you are done.

---

**Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.**

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## CRITICAL: Before ANY response (including questions)
- For ANY question about this codebase → call serena (or graphtr if this project has it set up) FIRST, then answer
- For external library/SDK questions → use context7 first, then serena for codebase integration
- Do NOT answer from training memory alone if the question involves code in this repo

## 0. Critical Thinking

**Actively challenge the user's reasoning — don't just execute.**

When the user makes a claim, proposes an approach, or describes a problem:
- Flag implicit assumptions they haven't stated.
- Identify logical gaps, missing context, or weak evidence.
- Name cognitive biases that might be shaping their framing.
- Surface uncomfortable truths they might be avoiding or overlooking.
- If their conclusion doesn't follow from their premises, say so directly.

This applies to technical decisions AND general reasoning. Sycophantic agreement is a failure mode.

Tone: direct, respectful, constructive. Never harsh, never dismissive.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

> **Simplicity vs Surgical tiebreaker:** If simplifying requires touching code outside the current scope, mention it to the user — don't do it silently.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

- If you notice a real bug in adjacent code (not dead code): mention it to the user — don't fix it silently.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

- If stuck after 2 attempts at the same sub-task: stop, name what's blocking, ask the user.

## 5. Sensitive Data

- Never hardcode secrets, API keys, or credentials in code.
- Never log or print credential values.
- If a task requires a secret, reference an environment variable — do not suggest inlining the value.

## 6. Verification via Sandbox (MANDATORY)

After any bug fix or logic change → invoke the **"verification" skill** before claiming done.
Never say "looks correct" without running it. PASS evidence required.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## MCP & Context Optimization

For any research, code navigation, or multi-source gathering → invoke the **"mcp-workflow" skill**.

Quick reference (full detail in skill):
- External library/SDK → `context7` first
- Internal codebase → `serena` first
- Large output / multi-command → `ctx_batch_execute`
- `graphtr-out/` exists → use graphtr before serena deep-reads

---

## graphtr

If this project has a `graphtr-out/` directory, it's a local snapshot of the hoton-graphtr
MCP server's code graph for this repo (see the `graphtr` skill for the full workflow).

Rules:
- For codebase questions, first run `python3 graphtr-out/query.py query "<keyword>"` when graphtr-out/graph.json exists. Use `graphtr-out/query.py path "<A>" "<B>"` for relationships and `graphtr-out/query.py explain "<name>"` for a node + its neighbors. These return a scoped subgraph, usually much smaller than raw grep output.
- If the script errors or the graph looks stale, fall back to `mcp__hoton-graphtr__query_code_graph` with the `user_id`/`repo_id` from `graphtr-out/manifest.json`.
- Open `graphtr-out/graphtr.html` in a browser for a visual, interactive view.
- Refreshing the graph after code changes is a re-export from hoton-graphtr, not a local rebuild — see the `graphtr` skill's Refresh flow; do not call `ingest_codebase` again (it mints a new `repo_id` and creates a duplicate graph).
- If there's no `graphtr-out/` yet, this section doesn't apply — skip it and use `serena` directly.

---

<!--
TOOLKIT NOTE: everything above this line is generic and came from
~/dotfiles/claude-toolkit. Add project-specific sections below
(Project Overview, Service Map, Key Commands, Architecture, etc.) —
those don't belong in the shared template.
-->

## Project Overview

HoTon-GrapHTR is a FastAPI service combining code-aware RAG (retrieval-augmented
generation) with a code knowledge graph. It exposes both REST endpoints and an
MCP tool surface (`app/mcp_server.py`, mounted into the FastAPI app via
`mcp.streamable_http_app()`) for agent/tool integration — e.g. it's the backend
the `graphtr` MCP server and skill (referenced above) talk to.

## Commands

```bash
pip install -r requirements.txt

# Run (needs Qdrant/Neo4j/Postgres reachable per .env)
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030

# Run zero-service, no external DBs (writes to graphtr-out/ instead)
DEPLOY_MODE=local uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8030

# Docker (app + Qdrant + Neo4j + Postgres)
docker compose -f docker/docker-compose.yml up --build

# Tests
pytest
pytest tests/test_graph_query.py            # single file
pytest tests/test_graph_query.py::test_name # single test
```

`install.sh` / `uninstall.sh` at the repo root handle the zero-service
(`DEPLOY_MODE=local`) setup/teardown end-to-end — see README.md for the full
flow (fresh-machine clone vs. in-place, what gets removed).

## Architecture

**Dual deploy mode is the central design constraint.** Every stateful backend
in this app has two implementations selected at runtime by
`Settings.deploy_mode` (`app/config.py`): `"server"` (Qdrant/Neo4j/Postgres,
the default) or `"local"` (file-backed, everything under
`Settings.local_data_dir`, default `./graphtr-out/`). When touching any of
these, both branches need to keep working:

| Concern | `server` | `local` | Dispatch point |
|---|---|---|---|
| Vectors | Qdrant over HTTP (`qdrant_url`) | embedded Qdrant at `local_data_dir/qdrant` | `app/clients/qdrant_store.py::get_qdrant_client` |
| Code graph | Neo4j (`GraphStore` over bolt) | `SqliteGraphStore` at `local_data_dir/graph.sqlite` | `app/graph/code_graph_store.py` (~line 584) |
| Usage tracking | `PostgresUsageStore` | `SqliteUsageStore` at `local_data_dir/usage.sqlite` | `app/dashboard/usage_store.py::get_usage_store` |

Switching `DEPLOY_MODE` does not migrate data between the two stores — they
are independent.

**App wiring (`app/main.py`):** `create_app()` takes every dependency
(`qdrant_client`, `embedder`, `llm`, `graph_store`, `usage_store`, etc.) as an
optional constructor argument, defaulting to the real `get_*()` factories when
omitted. Tests construct `create_app()` with fakes/in-memory instances
instead of monkeypatching — see `tests/conftest.py`'s `FakeGraphStore` and the
in-memory `QdrantClient(":memory:")` fixture for the pattern to follow.

**Module layout (`app/`):**
- `agentic/` — ReAct loop (`react.py`), HyDE (`hyde.py`), CRAG-style grading and
  SearXNG web search (`grading.py`), query complexity routing (`routing.py`).
- `clients/` — thin wrappers around Qdrant, the embedding model, the LLM, and
  a browser-automation service used for URL ingestion.
- `graph/` — code graph pipeline: `repo_source.py` resolves a repo to ingest,
  `code_parser.py` (tree-sitter) + `entity_extraction.py`/`entity_linker.py`
  build symbols/edges, `graph_pipeline.py` orchestrates ingestion,
  `code_graph_store.py` is the `GraphStore` abstraction (Neo4j/SQLite),
  `graph_query.py` implements BFS/shortest-path/explain queries,
  `repo_watcher.py` watches a checkout and reindexes on change.
- `rag/` — document ingestion/chunking (`chunker.py`, `documents.py`),
  retrieval (`retrieval.py`), self-consistency/context assembly
  (`context.py`), user memory and profile stores (`memory.py`, `profile.py`),
  and a background expiry job (`cleanup.py`).
- `dashboard/` — usage tracking (`tracker.py`, `usage_store.py`), health
  (`health.py`), aggregate queries (`queries.py`), and the router. Dashboard
  auth (`router.py`) is HTTP Basic and **fails open**: if
  `dashboard_user`/`dashboard_password` are unset, dashboard routes serve
  unauthenticated rather than 503 — deliberate, not an oversight.

**MCP tool surface (`app/mcp_server.py`):** `build_tool_context()` bundles all
the same dependencies into a `ToolContext` dataclass; `build_mcp_server()`
registers `FastMCP` tools that close over that context (RAG retrieval, memory,
profile, graph query, code ingestion). This is the second consumer of the
same dependency set `create_app()` wires up — when adding a new backend
dependency, both places usually need it.

**`scripts/`** — standalone tools distinct from the app runtime:
`build_knowledge_skeleton.py` / `index_knowledge.py` build and index a
knowledge base, `init_graphtr_skills.py` bootstraps the `graphtr` skill/MCP
config for a target project (this is what `install.sh` calls).
