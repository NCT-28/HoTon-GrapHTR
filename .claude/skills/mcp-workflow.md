# MCP & Context Optimization Workflow

## context-mode Tool Hierarchy (use in this order)

1. `ctx_batch_execute(commands, queries)` — primary research tool; runs shell commands in parallel, auto-indexes output, and returns matching snippets in one round trip. Use for gathering info across multiple sources.
2. `ctx_search(queries)` — search anything already indexed (prior commands, session memory). Batch multiple questions in one array call.
3. `ctx_execute(language, code)` — process/aggregate data already in the sandbox (filter, count, parse). Only what you `console.log()` enters the conversation; raw bytes stay in sandbox.
4. `ctx_execute_file(path, language, code)` — analyze/summarize/extract from a single file without loading its raw bytes into the conversation. Use instead of Read when you don't need to Edit the file.
5. `ctx_fetch_and_index(url)` — fetch external URLs; results indexed for `ctx_search`, raw page bytes never enter context.

## When NOT to use context-mode — use plain Bash when:
- Observing short, fixed output (e.g. `git status`, `whoami`, `pwd`)
- Mutating state (`git commit`, `mkdir`, `rm`, `mv`, file writes)
- Using Read/Edit/Write tools (they need exact bytes in conversation to match against)

## Priority Tooling
- External library, framework, or SDK → start with `context7`
- Internal codebase logic → start with `serena`
- Code that wraps or calls an external library from within this repo → `context7` first for the library's API contract, then `serena` for how this repo wires it in
- If this project has a code graph set up (see the `graphtr` skill / `graphtr-out/`), use it to scope down before `serena` deep-reads — graph queries return a small subgraph, not whole files
- If `graphtr-out/` does not exist, skip the graphtr steps below and use `serena` directly. `graphtr-out/` is project-specific (backed by the hoton-graphtr MCP server), not something this toolkit installs — see the `graphtr` skill if the project has one.

## Token Efficiency
- Before reading multiple files manually with `ls` or `cat`, use `serena`'s search and indexing tools to identify and fetch only the relevant code snippets
- Avoid asking the model to infer or guess external APIs — use `context7` to retrieve authoritative documentation instead
- If `graphtr-out/` exists, query it before broad file reads — it returns a scoped subgraph (usually much smaller than raw grep output)

## Workflow

1. Use `context7` to fetch the latest official documentation when the task involves external frameworks, libraries, or SDK APIs.
2. If `graphtr-out/` exists, use `scripts/query.py --out-dir graphtr-out query "<keyword>"` to locate relevant nodes and edges before touching the codebase.
   - Use `scripts/query.py --out-dir graphtr-out path "<A>" "<B>"` to trace relationships between two symbols.
   - Use `scripts/query.py --out-dir graphtr-out explain "<concept>"` for focused concept deep-dives.
   - If the script errors or looks stale, fall back to `mcp__hoton-graphtr__query_code_graph` (see the `graphtr` skill).
3. Use `serena` to get a high-level overview of the project structure if it is not already in context.
4. Use `serena` to locate specific logic or variable definitions across the codebase instead of performing broad file reads.
5. Only request full file context if `serena`'s summaries and `graphtr`'s results are insufficient — specifically: more than 3 ambiguous candidates returned, or the function exceeds ~100 LOC and full logic is needed.
6. When gathering info from multiple sources in parallel (grep, find, git log, etc.), use `ctx_batch_execute` instead of sequential Bash calls — output is auto-indexed and stays out of context.
7. After indexing, use `ctx_search` to query results rather than re-reading raw output.
8. To filter, count, or transform gathered data, use `ctx_execute` — only what you `console.log()` enters the conversation.

## Tool Failure Fallback
- If a tool fails or returns no results: fall back to the next tool in the priority chain (graphtr → serena → direct file read)
- If `context7` fails or has no results for the library: say so, then fall back to the library's official docs via `WebFetch`/`WebSearch` before guessing from memory
- If `context-mode` (ctx_batch_execute/ctx_execute/ctx_search/ctx_fetch_and_index) is unavailable or errors: fall back to plain Bash/Read for the same gathering step
- If all tools fail, notify the user before proceeding with direct file reads

## Context Maintenance
- Use `serena` to update or refresh internal project context when moving between different modules
- Re-query `context7` when framework versions, APIs, or external dependencies may have changed
- If `graphtr-out/` exists, refresh it at the end of a working session (not after every file edit) per the `graphtr` skill's Refresh flow (re-export from hoton-graphtr, not a rebuild-from-scratch)
