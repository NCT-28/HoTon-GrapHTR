---
name: graphtr
description: Use when answering a codebase question in this project and graphtr-out/ exists, or when asked to build/refresh/view the hoton-graphtr code graph. Covers offline graph queries (query/path/explain), the interactive graphtr.html viewer, and the one-shot ingest_codebase call that (re)creates graphtr-out/.
---

# graphtr

## Overview

`graphtr-out/` is a local snapshot of this repo's code graph, written directly by
`ingest_codebase` — no server-side state behind it. `repo_id` lives in
`graphtr-out/manifest.json`. Query it before grepping or reading files broadly.

To set up this same graphtr + graphtr-knowledge workflow in a **different** project, run
`python3 scripts/init_graphtr_skills.py <target-project-path>` from this repo — it
copies both skills (and the generic pipeline scripts they need) into the target.

## Args

`/graphtr <init|refresh>` (or a plain query with no arg — see Fast path below).
No arg + no `graphtr-out/graph.json` → treat as `init`. "code changed"/"stale"/"rebuild"
phrasing → `refresh`.

| Arg | Runs | Use when |
|---|---|---|
| `init` | Bootstrap section | `graphtr-out/graph.json` doesn't exist yet — first-time build |
| `refresh` | Bootstrap section again | code changed since last ingest — every call is a full reparse, that's expected |
| *(no arg, graph exists)* | Fast path | answering a codebase question — query `graph.json` directly, don't rebuild anything |

## Fast path — graph already exists

If `graphtr-out/graph.json` exists and the question is about codebase structure or relationships
("what calls X", "how does Y connect to Z", "explain W"), query it directly — do not rebuild.

```bash
python3 scripts/query.py --out-dir graphtr-out query "<keyword>"      # keyword BFS, depth 2
python3 scripts/query.py --out-dir graphtr-out path "<from>" "<to>"    # shortest path between two symbols
python3 scripts/query.py --out-dir graphtr-out explain "<name>"        # node + direct neighbors
```

Pure stdlib, no MCP round trip, no network. This is the only query path — there is no MCP
tool fallback (the graph is not server-resident).

For a visual, interactive view: open `graphtr-out/graphtr.html` in a browser (vis-network —
search box, click-for-detail info panel, kind legend with show/hide). Needs internet on first
load (CDN-hosted vis-network, like graphify-out/graph.html).

## Bootstrap — graphtr-out/ doesn't exist yet (or refresh)

One call: `mcp__hoton-graphtr__ingest_codebase(source="<repo path>")` → parses the repo and
writes `graphtr-out/graph.json`, `graphtr-out/manifest.json`, and `graphtr-out/graphtr.html`
into that repo directly. Returns `{repo_id, symbol_count, edge_count}`.

`source` is a local path only — **git URLs are not supported**; clone the repo first and pass
its path. Where that path needs to point depends on how the hoton-graphtr server you're
talking to is deployed:
- **Zero-service (`DEPLOY_MODE=local`, e.g. installed via `install.sh`)**: the server runs as a
  plain `uvicorn` process on the host, no container boundary — pass the repo's actual host path
  (e.g. `/Users/you/projects/<repo>`) straight through.
- **Docker (`docker compose -f docker/docker-compose.yml up`)**: hoton-graphtr only sees paths
  under its `code-repos` bind mount (`docker-hoton-graphtr-1` container) — if the repo isn't
  there yet, copy/rsync it in first (exclude `.git`, `node_modules`, `target`, build output),
  then pass the in-container path (`/data/code-repos/<repo>`).

The call can take a while on a big repo (full parse, every time — no incremental reindex).
There is no separate export/write/build step: writing `graphtr-out/` and rendering
`graphtr.html` both happen inside this one call.

**Refresh is the same call.** Re-run `ingest_codebase(source="<repo path>")` after code
changes; it reparses from scratch and overwrites `graphtr-out/`. There is no watcher and no
incremental reindex — every call mints a fresh `repo_id`, which is expected, not a mistake to
avoid or dedupe against.

To regenerate only the viewer after hand-editing `graph.json` (no re-ingest):
`python3 scripts/build_viewer.py --out-dir graphtr-out`.

## Knowledge base (narrative docs) — see the graphtr-knowledge skill

`graphtr-out/knowledge/*.md` (narrative docs on architecture/concerns/conventions/integrations/
stack/structure/testing, indexed into hoton-graphtr's RAG) is a separate, opt-in pipeline — see the
`graphtr-knowledge` skill (`.claude/skills/graphtr-knowledge/SKILL.md`) for the full workflow.
Not run automatically by Bootstrap above.

## Quick reference

| Need | How |
|---|---|
| Keyword search | `scripts/query.py --out-dir graphtr-out query "<kw>"` |
| Path between two symbols | `scripts/query.py --out-dir graphtr-out path "<a>" "<b>"` |
| Node + neighbors | `scripts/query.py --out-dir graphtr-out explain "<name>"` |
| Visual browse | open `graphtr-out/graphtr.html` |
| Stats overview | read `graphtr-out/manifest.json` |
| repo_id | `graphtr-out/manifest.json` |
| Regenerate viewer only (graph.json unchanged) | `python3 scripts/build_viewer.py --out-dir graphtr-out` |

## Common mistakes

- Calling `mcp__hoton-graphtr__ingest_codebase` with a host path (`/Users/...`) when the server is
  the **Docker** deploy — it only sees its bind mount (`/data/code-repos/...`); copy the repo into
  the mount first. Not an issue for the zero-service deploy (`DEPLOY_MODE=local`) — that server
  runs on the host, so a host path works directly.
- Passing a git URL — not supported; clone the repo and pass its path instead.
- Re-running the whole Bootstrap pipeline for a single query — if `graphtr-out/graph.json`
  already exists, query it directly instead.
