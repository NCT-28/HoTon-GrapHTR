---
name: graphtr
description: Use when answering a codebase question in this project and graphtr-out/ exists, or when asked to build/refresh/view the hoton-graphtr code graph. Covers offline graph queries (query/path/explain), the interactive graphtr.html viewer, and the ingest -> export -> write -> build pipeline that (re)creates graphtr-out/.
---

# graphtr

## Overview

`graphtr-out/` is a local snapshot of the `hoton-graphtr` MCP server's Neo4j code graph for this repo
— what `graphify-out/` used to serve, now backed by hoton-graphtr. `repo_id` and `user_id` live in
`graphtr-out/manifest.json`. Query it before grepping or reading files broadly.

To set up this same graphtr + graphtr-knowledge workflow in a **different** project, run
`python3 scripts/init_graphtr_skills.py <target-project-path>` from this repo — it
copies both skills (and the generic pipeline scripts they need) into the target.

## Args

`/graphtr <init|update|refresh>` (or a plain query with no arg — see Fast path below).
No arg + no `graphtr-out/graph.json` → treat as `init`. "rebuild"/"lost the watcher" phrasing →
`update`. "code changed"/"stale" phrasing → `refresh`.

| Arg | Runs | Use when |
|---|---|---|
| `init` | Bootstrap section | `graphtr-out/graph.json` doesn't exist yet — first-time build |
| `update` | Bootstrap section, with a **fresh** `ingest_codebase` call | the existing `repo_id`/watcher was lost entirely (container restarted without persistent state) — an intentional full rebuild, not a routine op. Afterward, check Neo4j for leftover duplicate `repo_id`s per the Refresh section's last paragraph and delete them |
| `refresh` | Refresh section | code changed since last ingest and the watcher/`repo_id` are still alive — reuse the existing `repo_id`, re-export only, no new ingest |
| *(no arg, graph exists)* | Fast path | answering a codebase question — query `graph.json` directly, don't rebuild anything |

## Fast path — graph already exists

If `graphtr-out/graph.json` exists and the question is about codebase structure or relationships
("what calls X", "how does Y connect to Z", "explain W"), query it directly — do not rebuild.

```bash
python3 scripts/query.py --out-dir graphtr-out query "<keyword>"      # keyword BFS, depth 2
python3 scripts/query.py --out-dir graphtr-out path "<from>" "<to>"    # shortest path between two symbols
python3 scripts/query.py --out-dir graphtr-out explain "<name>"        # node + direct neighbors
```

Pure stdlib, no MCP round trip, no network. If `graph.json` looks stale or the script errors,
fall back to `mcp__hoton-graphtr__query_code_graph` — same three modes (`mode="query"|"path"|"explain"`),
using the `user_id`/`repo_id` from `graphtr-out/manifest.json`.

For a visual, interactive view: open `graphtr-out/graphtr.html` in a browser (vis-network —
search box, click-for-detail info panel, kind legend with show/hide). Needs internet on first
load (CDN-hosted vis-network, like graphify-out/graph.html).

## Bootstrap — graphtr-out/ doesn't exist yet

1. **Ingest**: `mcp__hoton-graphtr__ingest_codebase(user_id, source="<repo path>")` → returns
   `repo_id`. `source` is a plain local path or git URL (`app/graph/repo_source.py` resolves it
   directly) — where that path needs to point depends on how the hoton-graphtr server you're
   talking to is deployed:
   - **Zero-service (`DEPLOY_MODE=local`, e.g. installed via `install.sh`)**: the server runs as a
     plain `uvicorn` process on the host, no container boundary — pass the repo's actual host path
     (e.g. `/Users/you/projects/<repo>`) straight through.
   - **Docker (`docker compose -f docker/docker-compose.yml up`)**: hoton-graphtr only sees paths
     under its `code-repos` bind mount (`docker-hoton-graphtr-1` container) — if the repo isn't
     there yet, copy/rsync it in first (exclude `.git`, `node_modules`, `target`, build output),
     then pass the in-container path (`/data/code-repos/<repo>`).

   The call can take a while and may time out at the MCP layer on a big repo; ingestion keeps
   running server-side regardless — check server logs for progress (`docker logs
   docker-hoton-graphtr-1` for the Docker deploy, or the `install.sh`-started log file for
   zero-service), and read `repo_id` back from Neo4j if the response didn't arrive.
2. **Export**: `mcp__hoton-graphtr__export_graph_snapshot(user_id, repo_id)` → returns
   `{repo_id, node_count, edge_count, node_kinds, edge_types, last_indexed_at, code_symbol_count,
   nodes, edges}`. `last_indexed_at` and `code_symbol_count` (code-symbol vector count for this
   repo — resolved per-repo under `DEPLOY_MODE=local`, see `app/clients/qdrant_store.py`'s
   `count_code_symbol_embeddings`) can be `null` if the repo isn't registered yet.
3. **Write**: `graphtr-out/graph.json` (`{nodes, edges}`) and `graphtr-out/manifest.json`
   (`repo_id`, `user_id`, counts, `node_kinds`, `edge_types`, `last_indexed_at`,
   `code_symbol_count`, `exported_at`).
4. **Build viewer**: `python3 scripts/build_viewer.py --out-dir graphtr-out` → generates `graphtr-out/graphtr.html`
   (under a second — layout runs live in the browser via vis-network, not precomputed). The sidebar's
   "Repo Overview" panel (symbol-kind/edge-type breakdown, code-vector count, last-indexed time) is
   baked from `graph.json`/`manifest.json` at this step — a static snapshot, viewable by opening the
   file directly with no server running.

## Refresh — code changed since last ingest

**Do NOT call `ingest_codebase` again for a repo that's already ingested and watched** —
`ingest_codebase_impl` mints a brand-new `repo_id` (`uuid.uuid4()`) on every call, so re-ingesting
the same source creates a duplicate, orphaned graph (and a duplicate background watcher that
keeps running forever, doing redundant reindex work) instead of updating the existing one.

The watcher set up on first ingest DOES auto-reindex the *existing* `repo_id` when files under
the ingested path change — but on a Docker Desktop bind mount (macOS/Windows), it's **poll-based
with real latency (up to ~1-2 minutes observed), not instant inotify**. A query run right after
editing files can legitimately look stale even though the watcher will catch up shortly. Confirmed
by testing: re-exporting the *same, unchanged* `repo_id` a couple minutes after a `query.py`
lookup came back stale showed the new symbols with no re-ingest needed.

If the source lives in the hoton-graphtr container's `code-repos` bind mount as a copy of this repo
(not a live mount), re-sync it first (e.g. `rsync -a --delete` from the host repo into
`docker/code-repos/<repo>/`, excluding `.git`, `node_modules`, `target`, `graphtr-out`) so the
watcher has something new to pick up — then:

1. **Wait a bit, then just re-export** (reuse the *same* `repo_id` from `graphtr-out/manifest.json`):
   `mcp__hoton-graphtr__export_graph_snapshot(user_id, repo_id)`.
2. If the export still doesn't show the new symbols after a couple minutes, retry the export
   again before assuming the watcher is broken and reaching for `ingest_codebase` — a premature
   re-ingest is what creates the duplicate-watcher problem above.
3. Overwrite `graphtr-out/graph.json` / `manifest.json` with the result.
4. `python3 scripts/build_viewer.py --out-dir graphtr-out`.

Only re-run full Bootstrap (with a fresh `ingest_codebase` call) if the repo_id/watcher was lost
entirely (e.g. container restarted without persistent state) — and if you do, check
`MATCH (n:CodeSymbol) RETURN DISTINCT n.repo_id` in Neo4j afterward for leftover duplicate
`repo_id`s from past accidental re-ingests, and delete their nodes
(`MATCH (n:CodeSymbol {repo_id:$rid}) DETACH DELETE n`) — they accumulate silently otherwise, each
with its own live watcher. Refresh is not needed after every edit — do it at the end of a working
session, or when a query result looks stale.

## Knowledge base (narrative docs) — see the graphtr-knowledge skill

`graphtr-out/knowledge/*.md` (narrative docs on architecture/concerns/conventions/integrations/
stack/structure/testing, indexed into hoton-graphtr's RAG) is a separate, opt-in pipeline — see the
`graphtr-knowledge` skill (`.claude/skills/graphtr-knowledge/SKILL.md`) for the full workflow.
Not run automatically by Bootstrap/Refresh above.

## Quick reference

| Need | How |
|---|---|
| Keyword search | `scripts/query.py --out-dir graphtr-out query "<kw>"` |
| Path between two symbols | `scripts/query.py --out-dir graphtr-out path "<a>" "<b>"` |
| Node + neighbors | `scripts/query.py --out-dir graphtr-out explain "<name>"` |
| Visual browse | open `graphtr-out/graphtr.html` |
| Stats overview | read `graphtr-out/manifest.json` |
| repo_id / user_id | `graphtr-out/manifest.json` |
| Regenerate viewer only (graph.json unchanged) | `python3 scripts/build_viewer.py --out-dir graphtr-out` |

## Common mistakes

- Calling `mcp__hoton-graphtr__ingest_codebase` with a host path (`/Users/...`) when the server is
  the **Docker** deploy — it only sees its bind mount (`/data/code-repos/...`); copy the repo into
  the mount first. Not an issue for the zero-service deploy (`DEPLOY_MODE=local`) — that server
  runs on the host, so a host path works directly.
- Re-running the whole Bootstrap pipeline for a single query — if `graphtr-out/graph.json`
  already exists, query it directly instead.
- Using a different `user_id` per call — ingest/export/query must share the same one, or the
  graph lookups return empty. Reuse the value in `graphtr-out/manifest.json`.
- Calling `ingest_codebase` again to "refresh" — it always mints a new `repo_id`, creating a
  duplicate graph instead of updating the existing one. Use the Refresh flow (re-export only) instead.
