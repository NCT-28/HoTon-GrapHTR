---
name: graphtr-knowledge
description: Use when asked to build, refresh, or regenerate the graphtr knowledge base — narrative docs on architecture/concerns/conventions/integrations/stack/structure/testing indexed into hoton-graphtr's RAG — or when asked to "tạo knowledge"/"index knowledge into RAG" for this project. Separate from the graphtr skill's graph (graph.json) pipeline; this covers the narrative-doc pipeline only.
---

# graphtr-knowledge

## Overview

`graphtr-out/knowledge/*.md` holds 7 narrative topic docs (architecture, concerns, conventions,
integrations, stack, structure, testing), indexed into `hoton-graphtr`'s document RAG under a
dedicated `rag_user_id` (stored in `graphtr-out/manifest.json`, separate from the graph's
`user_id` string — RAG document ownership requires a real UUID, see the `graphtr` skill's
Bootstrap section for that distinction).

Generator scripts live in `scripts/` (the service that produces this output), not in
`graphtr-out/` itself — `graphtr-out/` is treated as a pure output directory.

This is **not** run automatically by the `graphtr` skill's Bootstrap/Refresh — it costs real
Claude turns to fill in narrative, so it's a manual, occasional step, invoked on its own.

## Args

`/graphtr-knowledge <init|update|refresh>`. No arg → infer from context (first run with no
`graphtr-out/knowledge/` dir means `init`; "regenerate"/"facts changed" phrasing means `update`;
"reindex"/"push again" phrasing means `refresh`). Ask if genuinely ambiguous.

| Arg | Runs (Workflow steps below) | Use when |
|---|---|---|
| `init` | 1 → 2 → 3 (all 7 topics) | `graphtr-out/knowledge/` doesn't exist yet — first-time build |
| `update` | 1 → 2 → 3, scoped to the topic(s) whose facts changed (e.g. `python3 scripts/build_knowledge_skeleton.py stack conventions`) | underlying facts changed materially (real refactor, new service, etc.) for specific topic(s). Step 1 only rewrites the `## Facts` section of the topics you name and auto-preserves each file's existing `## Narrative` (it's only reset to a TODO placeholder the first time a file is created) — so step 2 only needs new prose if the facts change actually invalidated what was written, not for all 7 |
| `refresh` | 3 only, scoped to the topic(s) that changed (e.g. `python3 scripts/index_knowledge.py stack`) | narrative docs are already correct and unchanged, just need to (re)push them into RAG — e.g. `hoton-graphtr` was redeployed and lost its documents, or you hand-edited a `.md` file directly and want it reindexed without touching skeletons. Scoping to specific topics avoids paying the ~2-2.5min/doc indexing cost for docs that didn't change |

## Workflow

1. **Generate skeletons**: `python3 scripts/build_knowledge_skeleton.py [topic ...]` —
   regenerates fact skeletons from `graphtr-out/graph.json` + `CLAUDE.md` + repo manifests
   (`Cargo.toml`/`package.json`/`requirements.txt`) into `graphtr-out/knowledge/<topic>.md`. With no
   args, does all 7; pass topic names (e.g. `stack conventions`) to scope it to just those. Each
   skeleton has a `## Facts` section (auto-filled) and a `## Narrative` section — left as a
   `<!-- TODO: fill in -->` placeholder the first time a topic's file is created, but preserved
   as-is on every later regeneration of that same topic (only the Facts section is replaced).

   Only regenerate a topic when its underlying facts have materially changed (e.g. after a real
   refactor), not as a routine step — regeneration is safe for narrative (it's preserved) but
   still pointless churn if nothing about that topic's facts actually changed.

2. **Fill narrative**: for each of the 7 files, read the relevant source (per topic, see table
   below), then edit the file in place, replacing the `<!-- TODO: fill in -->` block with real
   prose. Do not leave any `<!-- TODO -->` markers behind — verify with
   `grep -rn "TODO: fill in" graphtr-out/knowledge/`.

   | Topic | Read before writing |
   |---|---|
   | `architecture.md` | `CLAUDE.md` `## Architecture` section; `python3 graphtr-out/query.py explain <key module>` for actual call graph |
   | `concerns.md` | `.claude/skills/graphtr/SKILL.md` (watcher latency, `ingest_codebase` re-mint gotcha); `CLAUDE.md` (deprecated `graphify`, `sd-server` model-family limit) — no auto-filled facts, narrative only |
   | `conventions.md` | `CLAUDE.md` (Biome not Prettier); `git log --oneline -20` for commit style; check for `rustfmt.toml`/`biome.json` presence |
   | `integrations.md` | `CLAUDE.md` `### External integrations` + `### Environment variables` sections |
   | `stack.md` | `CLAUDE.md` (why each service picked its stack); each service's manifest file |
   | `structure.md` | `CLAUDE.md` Service Map; each service's own top-level subdirectory listing |
   | `testing.md` | Each service's test commands/config; note any service with no detected test tooling as a real coverage gap, not a script limitation |

3. **Index into RAG**: `python3 scripts/index_knowledge.py [topic ...]` — mints a `rag_user_id`
   UUID into `graphtr-out/manifest.json` if absent, then for each topic (all 7 by default, or just
   the ones named): deletes any existing doc with a matching title, then uploads via
   `hoton-graphtr`'s `POST /api/documents`.

   Requires `docker-hoton-graphtr-1` running and reachable at `http://localhost:8030` (override with
   `RAG_SERVICE_URL` env var).

   **Important — title matching:** `hoton-graphtr`'s upload endpoint derives `title` from the
   uploaded filename's stem, not any field the client sends — `architecture.md` is stored with
   title `architecture` (lowercase), never a capitalized display name. The delete-before-reupload
   logic matches on this same lowercase string.

   **Important — this is slow, not stuck:** each document upload triggers a server-side
   entity-extraction pass (local reasoning LLM) that blocks `hoton-graphtr`'s single event loop for
   ~2-2.5 minutes before the next request is served. A full 7-topic run takes ~15-20 minutes.
   Run it in the background and check back rather than assuming a long-running call has hung —
   confirm via `docker logs docker-hoton-graphtr-1 --tail 40 -t` that new `POST /api/documents`
   `202 Accepted` lines keep appearing over time.

## Retrieving the indexed knowledge

Once indexed, use `mcp__hoton-graphtr__retrieve_chunks(user_id=<rag_user_id>, query=...)` or
`mcp__hoton-graphtr__get_rag_context(user_id=<rag_user_id>, query=...)`, using the `rag_user_id` from
`graphtr-out/manifest.json` — not the graph's `user_id`.

## Verifying a run

```bash
python3 -c "
import json, urllib.request
rag_user_id = json.load(open('graphtr-out/manifest.json'))['rag_user_id']
req = urllib.request.Request('http://localhost:8030/api/documents', headers={'X-User-Id': rag_user_id})
data = json.loads(urllib.request.urlopen(req).read())
print(len(data['documents']), [d['title'] for d in data['documents']])
"
```
Expected: `7 ['architecture', 'concerns', 'conventions', 'integrations', 'stack', 'structure', 'testing']` (order may vary). Re-running `index_knowledge.py` should keep this at 7, not grow it — if it grows, the delete-before-reupload match failed (check the topic string used matches the stored title exactly).

## Common mistakes

- Re-running `build_knowledge_skeleton.py` for all 7 topics when only one changed — pass the specific topic name(s) instead to avoid pointless churn on unaffected files (narrative is preserved either way, but there's no reason to touch topics whose facts didn't change).
- Assuming a hung `index_knowledge.py` process is broken — check `docker logs docker-hoton-graphtr-1` timestamps first; the ~2-2.5min per-doc delay is real and pre-existing hoton-graphtr behavior, not something this workflow introduced or can speed up.
- Matching delete-before-reupload on a capitalized display title (`"Architecture"`) instead of the actual stored lowercase title (`"architecture"`) — the server always derives title from the filename stem.
- Assuming `graphtr-out/`'s `user_id` (a free string) can be reused for RAG calls — RAG document ownership requires the separate `rag_user_id` UUID.
