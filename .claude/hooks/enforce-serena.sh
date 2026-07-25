#!/usr/bin/env bash
# UserPromptSubmit hook: enforce MCP tool-routing at submit time.
# Encodes the routing rules from .claude/skills/mcp-workflow.md + CLAUDE.md so
# they sit in-context for the turn about to run. Text-only instructions get
# skipped; injecting here makes them hard to ignore.
#
# graphtr line is conditional: only injected when graphtr-out/ exists
# (mcp-workflow.md:19 -- "If graphtr-out/ does not exist, skip graphtr").

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"

GRAPHTR_RULE=""
if [ -d "${PROJECT_DIR}/graphtr-out" ]; then
  GRAPHTR_RULE=" (3b) graphtr-out/ EXISTS: run scripts/query.py --out-dir graphtr-out query \"<keyword>\" (or path/explain) to scope down to a small subgraph BEFORE serena deep-reads (graphtr -> serena)."
fi

CONTEXT="ENFORCED MCP ROUTING (CLAUDE.md + skills/mcp-workflow.md). Pick the FIRST tool by trigger, do NOT answer from memory: \
(1) Repo code question/edit (symbols, logic, files, behavior) -> serena FIRST (mcp__serena__find_symbol / get_symbols_overview / find_referencing_symbols / search_for_pattern) before answering or editing.${GRAPHTR_RULE} \
(2) External library/framework/SDK/API question -> context7 FIRST (resolve-library-id then query-docs); never guess external APIs from memory. \
(3) Gathering across multiple sources, large/processed output (grep+find+git log, parse/filter/count, test output, fetch URLs) -> context-mode: ctx_batch_execute / ctx_execute / ctx_search / ctx_fetch_and_index -- NOT sequential bash/cat. \
SKIP rule: plain Bash only for short fixed output (git status, pwd) or state mutation (git, mkdir, rm, mv, writes). The ONLY skip for serena is a pure meta question about a doc/config file already fully in this conversation -- say 'answering from already-read file, no serena needed' explicitly. If about to answer a code/lib question without the right tool, STOP and call it first."

# Emit JSON safely via jq so quoting/newlines can never break the payload.
jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: $ctx
  }
}'
