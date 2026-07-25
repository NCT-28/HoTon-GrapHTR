# Incremental Reindex — Design

## Context

`RepoWatcherManager.reindex()` (`app/graph/repo_watcher.py`) is the only path
that keeps a watched repo's code graph + code-symbol vectors in sync with
disk. On *any* relevant file change it currently does a full-repo pass: parse
every file with `parse_repo()`, delete every existing symbol/edge for the
repo (`replace_repo_graph`), re-insert everything, and delete+re-embed every
code-symbol vector for the repo (`_replace_symbol_embeddings`). Cost is
`O(total repo symbols)` per save, not `O(changed symbols)`.

The root blocker for doing better: `code_parser.py` assigns every symbol a
fresh `uuid.uuid4()` id on every parse, even for symbols whose source didn't
change. Without a stable identity across reindexes there is no way to tell
"this symbol is unchanged, skip re-embedding it" — this spec's first job is
fixing that, everything else follows from it.

This matters most for large repos in `DEPLOY_MODE=local` (per-repo storage,
see the 2026-07-23 zero-service spec), where a single-line save on a big
codebase currently re-embeds the entire symbol set on a CPU-bound
`sentence-transformers` model. The mechanism itself is deploy-mode-agnostic
(it lives in `GraphStore`/`code_parser.py`/`repo_watcher.py`), so both
`local` and `server` deploy modes benefit equally.

## Goal

A file save during an active watch only re-parses and re-embeds the file(s)
that actually changed. Symbols in untouched files keep their id, their
`content_hash`, and their existing vector — no re-embed, no DB churn.

## Non-goals

- No cross-file edge re-resolution beyond the files that changed in this
  debounce window. A rename in file A leaves stale `CALLS`/`IMPORTS`/
  `INHERITS` edges from an *unchanged* file B pointing at the old symbol
  until B is itself edited — accepted, self-healing, documented in Design §3.
- No migration script for previously-ingested repos (Rollout section).
- No change to `ingest_codebase`'s first-time full parse behavior, or to
  `reindex()` as a full-rebuild fallback — both keep working exactly as
  today, just now also computing deterministic ids/content hashes.
- No change to `DEPLOY_MODE` behavior or the local-mode per-repo storage
  split from the prior spec.

## Design

### 1. Symbol identity & content hash (`code_parser.py`)

`ParsedSymbol.id` changes from `uuid.uuid4()` to
`sha1(file_path + kind + qualified_name)`. `qualified_name` is built during
the existing recursive `walk()` by threading the enclosing symbol's name down
alongside the enclosing id already tracked today (e.g. `Module.ClassA.__init__`
vs `Module.ClassB.__init__` — deterministic, collision-resistant across
same-named symbols in different scopes within one file). `module` symbols use
`sha1(file_path + "module")`.

`ParsedSymbol` gains `content_hash = sha1(source_bytes[node.start_byte:node.end_byte])`
— the hash of the symbol's own body, from tree-sitter's byte range. Unrelated
edits elsewhere in the file don't change it; any edit to the symbol's own
body (including pure reformatting) does. Reformatting causing an
unnecessary-but-harmless re-embed is an accepted tradeoff over trying to
normalize whitespace.

Incidental fix: `mentions_edges` (text_entity → symbol) reference symbol ids.
Today those go stale on *every* reindex of the repo, not just ones touching
the mentioned symbol, because ids are random. With stable ids, a mentions
edge only goes stale if the symbol it targets is actually renamed or deleted.

### 2. Watcher: batch changed paths (`repo_watcher.py`)

`_RepoChangeHandler` currently restarts one debounce timer per relevant event
and, on fire, calls a zero-argument callback — no file path reaches
`reindex()` today. It changes to accumulate two sets across the debounce
window instead of firing blind:

- `changed_paths` — from `on_created` / `on_modified`, and the *destination*
  path of `on_moved`.
- `deleted_paths` — from `on_deleted`, and the *source* path of `on_moved`
  (a rename is modeled as delete-old + add-new, consistent with the accepted
  stale-edge policy in §3).

Debounce timing/mechanics are unchanged — still one timer per repo, still
`_DEFAULT_DEBOUNCE_SECONDS`. On fire, the accumulated sets are read and
cleared, and `reindex_paths(user_id, repo_id, local_path, changed_paths,
deleted_paths)` is called instead of `reindex()`.

A path can land in both sets within one window (e.g. modified then deleted
before the timer fires) — no special dedup is needed for this. `reindex_paths`
step 1 (§3) already checks whether a `changed_paths` entry still exists on
disk before parsing it; if not, it's handled as deleted regardless of which
set(s) it ended up in. The union of both sets (§3 step 2) is what determines
"stale" either way.

`reindex()` (full parse) is kept as-is, used for: `ingest_codebase_impl`'s
first-time ingest, and as a manual full-rebuild fallback (not auto-invoked).

### 3. Incremental reindex orchestration (`RepoWatcherManager.reindex_paths`)

1. **Parse only what changed** — for each path in `changed_paths`, call the
   existing `_parse_file(path, ext)` (already file-scoped internally inside
   `parse_repo`'s loop, just not exposed outside it) → `new_symbols` + that
   file's own outgoing pending refs (`pending_calls/imports/inherits`) + its
   `DEFINES` pairs. A path that no longer exists by the time it's processed
   (created-then-deleted within one debounce window) is treated as deleted.
2. **Baseline** — `graph_store.get_subgraph(user_id, repo_id)` (existing
   method, no new call). Split returned nodes by `file_path`: nodes in
   `changed_paths ∪ deleted_paths` are **stale** (being replaced/removed);
   the rest are **kept baseline**.
3. **Resolve edges for changed files only** — build the
   `name_to_id`/`class_name_to_id`/`basename_to_module_id` index from
   `kept_baseline_nodes + new_symbols` (the index-build + resolve tail of
   `parse_repo` gets factored into a shared helper used by both the full and
   incremental paths, so resolution logic never drifts between them).
   Resolve `new_symbols`' pending refs against that index. Baseline files'
   existing edges are left untouched.
4. **Embed diff** — for each symbol in `new_symbols`, if a stale symbol
   existed with the *same id* and the *same `content_hash`*, it's unchanged:
   no re-embed. Otherwise it's marked new/changed.
5. **Scoped write** — new `GraphStore` method,
   `replace_files_in_repo(repo, stale_file_paths, symbols, edges)`: deletes
   only symbol/edge rows whose `file_path ∈ stale_file_paths`, inserts
   `new_symbols` + the edges resolved in step 3, leaves every other row in
   the repo untouched. `replace_repo_graph` (full wipe) is unchanged, still
   used by `reindex()`. This is implemented on `SqliteGraphStore`,
   `Neo4jGraphStore`, `FakeGraphStore` (test double), and
   `LocalMultiRepoGraphStore` (routes by `local_path` the same way
   `replace_repo_graph` already does) — the widest-blast-radius part of this
   design.
6. **Vector write** — see §4. Only symbols marked in step 4 get
   (re)embedded; only symbols that disappeared get their vector deleted.

### 4. Vector store: deterministic point id, incremental upsert/delete

Confirmed via Qdrant docs (context7): point ids must be a u64 integer or a
UUID — an arbitrary string (e.g. the raw sha1 hex id) is not accepted.

- **Point id** = `uuid.uuid5(NAMESPACE, symbol.id)` — deterministic (same
  input always produces the same UUID), valid for Qdrant, stable across
  reindexes as long as `symbol.id` doesn't change. `NAMESPACE` is one fixed
  `uuid.UUID` constant declared in `qdrant_store.py`.
- **Payload** unchanged in shape: `symbol_id` (the raw sha1 string, for
  filtering/debugging), `user_id`, `repo_id`, `name`, `kind`, `file_path`.
- **Selective upsert** — only symbols flagged in §3.4 get `embed_batch()` +
  `upsert()`. Unchanged symbols: zero Qdrant operations.
- **Selective delete** — only points for symbols that actually disappeared
  (removed from a file, or renamed so the old id no longer exists) get
  deleted, via `PointIdsList` of their specific point ids — not the
  blanket `user_id + repo_id` filter-delete `_replace_symbol_embeddings`
  uses today.
- `_replace_symbol_embeddings` (used by full `reindex()`) keeps its current
  wipe-then-reembed-everything behavior, just switches to
  `uuid5(symbol.id)` point ids instead of random `uuid4()` — so the first
  incremental reindex after any full reindex has correct ids to diff
  against.

### 5. Testing plan

TDD per layer, mirroring the existing test split:

- **`code_parser.py`**: id determinism across repeated parses of the same
  file; id changes on rename, stays stable on body-only edits;
  `content_hash` changes on body edits (including whitespace-only), stays
  stable when unrelated lines elsewhere in the file move; two same-named
  symbols in different scopes in one file get different ids; the factored
  index-build+resolve helper tested standalone against a mixed
  baseline+fresh-parsed input.
- **`repo_watcher.py`**: `_RepoChangeHandler` correctly buckets a burst of
  events into `changed_paths`/`deleted_paths` (create+modify same file →
  one entry; modify-then-delete → deleted only). `reindex_paths` on an
  N-file repo touching 1 file: only that file's symbols get
  (re)written/(re)embedded — asserted via embed-call count, the key proof
  the goal is met — and the other N-1 files' symbol ids/content hashes are
  untouched. File deletion removes its symbols + vectors, leaves others
  intact. The accepted-stale-edge case (rename in A, unchanged B still
  pointing at old id) gets an explicit test asserting *that* behavior, so
  it reads as an intentional contract, not a latent bug. Full existing
  `test_repo_watcher.py` suite (full `reindex()` path) must keep passing
  unmodified.
- **`code_graph_store.py`**: `replace_files_in_repo` tested per backend
  (`SqliteGraphStore`, `FakeGraphStore`, `LocalMultiRepoGraphStore` routing,
  `Neo4jGraphStore` integration test gated on `NEO4J_TEST_URL` like its
  siblings) — touching 1 of N files in a repo leaves the other files' rows
  untouched.
- **`qdrant_store.py`**: `uuid5(NAMESPACE, symbol_id)` determinism; a
  3-symbol repo with 1 changed symbol results in exactly 1 upsert call, 2
  points untouched.

## Rollout

- **No migration script.** Previously-ingested repos have `uuid4()`-based
  ids. After upgrade, the first reindex of any given repo (via watcher or
  manual refresh) sees every symbol as "new" (id mismatch against the old
  random ids) and does a one-time full re-embed — no worse than today's
  per-save cost, just paid once instead of every save. Incremental savings
  start from the second reindex onward.
- **Schema**: `code_symbols` gains a `content_hash TEXT` column.
  `SqliteGraphStore._init_schema()` adds it via a guarded
  `ALTER TABLE code_symbols ADD COLUMN content_hash TEXT` (sqlite has no
  `ADD COLUMN IF NOT EXISTS`) alongside the existing `CREATE TABLE IF NOT
  EXISTS`. Neo4j needs no schema migration (schema-less) — existing
  `CodeSymbol` nodes simply lack the property, which naturally reads as "hash
  changed" and triggers the same one-time re-embed described above.
- **No feature flag.** `reindex()`/watcher switch straight to
  `reindex_paths()` — no old/new path kept side by side.
- **Rollback**: revert the commit. The extra `content_hash` column is inert
  to old code; no data is left in a form old code can't read.
- **Deploy mode**: this change lives in `GraphStore`/`code_parser.py`/
  `repo_watcher.py` and applies identically to `DEPLOY_MODE=local` and
  `server` — unlike the prior per-repo-storage spec, it is not gated by
  deploy mode.
