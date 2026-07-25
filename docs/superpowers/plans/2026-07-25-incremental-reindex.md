# Incremental Reindex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A file save during an active repo watch only re-parses and re-embeds the file(s) that actually changed, instead of the whole repo — see `docs/superpowers/specs/2026-07-25-incremental-reindex-design.md` for the full design and rationale.

**Architecture:** Symbol ids become deterministic (`sha1(file_path+kind+qualified_name)`) with a companion `content_hash` so "unchanged" is detectable across reindexes. The watcher batches changed/deleted file paths per debounce window instead of firing blind. A new `reindex_paths()` re-parses only those files, merges them with the existing baseline symbol set to resolve cross-file edges, and writes/embeds only what changed via a new `GraphStore.replace_files_in_repo` method and deterministic Qdrant point ids.

**Tech Stack:** Python, tree-sitter (`tree_sitter_language_pack`), SQLite, Neo4j (`neo4j` driver), Qdrant (`qdrant_client`), `watchdog`, pytest.

---

## File Structure

- Modify `app/graph/code_parser.py` — deterministic id + `content_hash` on `ParsedSymbol`; factor the per-file parse loop into `parse_files()` and the index-build+resolve tail into `resolve_edges()`, reused by both `parse_repo()` (full) and the new incremental path.
- Modify `app/clients/qdrant_store.py` — `symbol_point_id()`: deterministic UUID5 Qdrant point id from a symbol's stable id.
- Modify `app/graph/code_graph_store.py` — `GraphStore.replace_files_in_repo` (new abstract method) implemented on `SqliteGraphStore`, `Neo4jGraphStore`, `LocalMultiRepoGraphStore`; `content_hash` column/property added everywhere symbols are read/written.
- Modify `tests/conftest.py` — `FakeGraphStore.replace_files_in_repo`.
- Modify `app/graph/repo_watcher.py` — `_RepoChangeHandler` batches changed/deleted paths per debounce window; new `reindex_paths()` orchestrates the incremental path; `_replace_symbol_embeddings` (full path) and the new `_update_symbol_embeddings` (incremental path) both use `symbol_point_id`.
- Test files: `tests/test_code_parser.py`, `tests/test_qdrant_store.py`, `tests/test_sqlite_graph_store.py`, `tests/test_code_graph_store_integration.py`, `tests/test_local_multi_repo_graph_store.py`, `tests/test_repo_watcher.py` all gain new cases; no new test files needed (each feature lands next to its existing suite).

---

## Task 1: Deterministic symbol id + content hash (`code_parser.py`)

**Files:**
- Modify: `app/graph/code_parser.py`
- Test: `tests/test_code_parser.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_code_parser.py`:

```python
def test_parse_repo_symbol_ids_are_deterministic_across_repeated_parses(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")

    symbols_1, _ = parse_repo(str(tmp_path))
    symbols_2, _ = parse_repo(str(tmp_path))

    ids_1 = {s.name: s.id for s in symbols_1}
    ids_2 = {s.name: s.id for s in symbols_2}
    assert ids_1 == ids_2


def test_parse_repo_id_and_content_hash_are_stable_across_an_unrelated_line_shift(tmp_path):
    """id is file_path+kind+qualified_name based, content_hash is a hash of the
    symbol's own byte range -- neither depends on line numbers, so inserting an
    unrelated blank line above a symbol must not change its id or content_hash,
    even though start_line/end_line shift."""
    path = tmp_path / "a.py"
    path.write_text("def foo():\n    pass\n\n\ndef bar():\n    pass\n")
    before = {s.name: (s.id, s.content_hash) for s in parse_repo(str(tmp_path))[0]}

    path.write_text("\ndef foo():\n    pass\n\n\ndef bar():\n    pass\n")  # blank line inserted above foo
    after = {s.name: (s.id, s.content_hash) for s in parse_repo(str(tmp_path))[0]}

    assert before["foo"] == after["foo"]  # id AND content_hash both stable
    assert before["bar"] == after["bar"]  # id AND content_hash both stable


def test_parse_repo_content_hash_changes_when_symbol_body_edited(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("def foo():\n    return 1\n")
    before = next(s for s in parse_repo(str(tmp_path))[0] if s.name == "foo")

    path.write_text("def foo():\n    return 2\n")
    after = next(s for s in parse_repo(str(tmp_path))[0] if s.name == "foo")

    assert before.id == after.id  # same file+kind+qualified_name -> same id
    assert before.content_hash != after.content_hash


def test_parse_repo_same_named_symbols_in_different_scopes_get_different_ids(tmp_path):
    (tmp_path / "a.py").write_text(
        "class A:\n"
        "    def __init__(self):\n"
        "        pass\n"
        "\n"
        "class B:\n"
        "    def __init__(self):\n"
        "        pass\n"
    )

    symbols, _ = parse_repo(str(tmp_path))

    init_ids = {s.id for s in symbols if s.name == "__init__"}
    assert len(init_ids) == 2


def test_parse_files_parses_only_the_given_paths(tmp_path):
    from app.graph.code_parser import parse_files

    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")

    symbols, defines, calls, imports, inherits = parse_files([str(tmp_path / "a.py")])

    names = {s.name for s in symbols}
    assert "foo" in names
    assert "bar" not in names


def test_resolve_edges_resolves_call_against_a_mixed_baseline_and_fresh_index():
    from app.graph.code_parser import resolve_edges

    # `bar` looks like it came from an already-indexed, unchanged file (a plain dict,
    # the same shape GraphStore.get_subgraph returns); `foo` is freshly parsed this pass.
    baseline = [{"id": "bar-id", "name": "bar", "kind": "function", "file_path": "b.py"}]
    fresh = [{"id": "foo-id", "name": "foo", "kind": "function", "file_path": "a.py"}]

    edges = resolve_edges(baseline + fresh, [], [("foo-id", "bar")], [], [])

    assert len(edges) == 1
    assert edges[0].source == "foo-id"
    assert edges[0].target == "bar-id"
    assert edges[0].type == "CALLS"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_code_parser.py -v`
Expected: the four new determinism/hash tests FAIL (ids are currently `uuid.uuid4()`, `ParsedSymbol` has no `content_hash` field — `AttributeError`); `parse_files`/`resolve_edges` import tests FAIL with `ImportError`.

- [ ] **Step 3: Rewrite `code_parser.py`**

Replace the whole file with:

```python
"""Tree-sitter based code-structure extraction: given a repo root, walks every
recognized source file and produces CodeSymbol nodes plus
DEFINES/CALLS/IMPORTS/INHERITS edges.

Cross-file resolution (a call/import/inherit referencing a symbol defined in
another file) is name-based: every file is parsed first, then edges are
resolved against a repo-wide name index. This is a deliberate simplification
— it can't disambiguate two same-named symbols in different files, which is
an accepted limitation for this phase, not a bug to chase.

INHERITS extraction is Python-only in this phase (via the `superclasses`
field on `class_definition`) — TypeScript/JavaScript/Rust class inheritance
requires heavier per-grammar handling than this phase's scope covers; those
languages still get DEFINES/CALLS/IMPORTS.

Symbol `id` is deterministic (sha1 of file_path+kind+qualified_name), not a
random uuid — a symbol keeps the same id across repeated parses as long as
its name/scope don't change, which incremental reindex (repo_watcher.py)
relies on to know "this symbol still exists" without re-embedding it.
`content_hash` (sha1 of the symbol's own source bytes) is the separate
signal for "did this symbol's content actually change"."""

import hashlib
import os
from dataclasses import dataclass

from tree_sitter_language_pack import get_parser

_IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "target", "__pycache__", "dist", "build"}


@dataclass
class LanguageConfig:
    parser_name: str
    definition_types: dict[str, str]   # tree-sitter node type -> symbol kind
    import_types: set[str]
    call_types: set[str]
    superclass_field: str | None = None


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    ".py": LanguageConfig(
        parser_name="python",
        definition_types={"function_definition": "function", "class_definition": "class"},
        import_types={"import_statement", "import_from_statement"},
        call_types={"call"},
        superclass_field="superclasses",
    ),
    ".ts": LanguageConfig(
        parser_name="typescript",
        definition_types={"function_declaration": "function", "class_declaration": "class", "method_definition": "method"},
        import_types={"import_statement"},
        call_types={"call_expression"},
    ),
    ".tsx": LanguageConfig(
        parser_name="tsx",
        definition_types={"function_declaration": "function", "class_declaration": "class", "method_definition": "method"},
        import_types={"import_statement"},
        call_types={"call_expression"},
    ),
    ".js": LanguageConfig(
        parser_name="javascript",
        definition_types={"function_declaration": "function", "class_declaration": "class", "method_definition": "method"},
        import_types={"import_statement"},
        call_types={"call_expression"},
    ),
    ".jsx": LanguageConfig(
        parser_name="javascript",
        definition_types={"function_declaration": "function", "class_declaration": "class", "method_definition": "method"},
        import_types={"import_statement"},
        call_types={"call_expression"},
    ),
    ".rs": LanguageConfig(
        parser_name="rust",
        definition_types={"function_item": "function", "struct_item": "class", "impl_item": "class"},
        import_types={"use_declaration"},
        call_types={"call_expression"},
    ),
}


@dataclass
class ParsedSymbol:
    id: str
    kind: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    language: str
    content_hash: str


@dataclass
class ParsedEdge:
    source: str
    target: str
    type: str


def _symbol_id(file_path: str, kind: str, qualified_name: str) -> str:
    return hashlib.sha1(f"{file_path}\x00{kind}\x00{qualified_name}".encode("utf8")).hexdigest()


def _content_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf8", errors="replace")


def _node_name(node, source: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    return _text(name_node, source) if name_node is not None else None


def _call_target_name(node, source: bytes) -> str | None:
    func_node = node.child_by_field_name("function")
    if func_node is None:
        return None
    text = _text(func_node, source)
    return text.rsplit(".", 1)[-1].rsplit("::", 1)[-1]


def _import_names(node, source: bytes) -> list[str]:
    """Best-effort: every dotted_name/identifier under an import node — this
    over-collects (e.g. both the module and the imported symbol name), which
    is fine since resolution below only keeps names that match a known module."""
    names: list[str] = []

    def walk(n):
        if n.type in ("dotted_name", "identifier"):
            names.append(_text(n, source))
            return
        for c in n.children:
            walk(c)

    walk(node)
    return names


def _superclass_names(node, config: LanguageConfig, source: bytes) -> list[str]:
    if config.superclass_field is None:
        return []
    field_node = node.child_by_field_name(config.superclass_field)
    if field_node is None:
        return []
    return [_text(c, source) for c in field_node.children if c.type == "identifier"]


def _parse_file(file_path: str, ext: str):
    """Returns (symbols, resolved_defines, pending_calls, pending_imports, pending_inherits)
    where resolved_defines is [(parent_id, child_id)] (already known within
    this file) and the pending_* lists are [(source_id, target_name)] to be
    resolved against a repo-wide name index by resolve_edges()."""
    config = LANGUAGE_CONFIGS[ext]
    parser = get_parser(config.parser_name)

    with open(file_path, "rb") as f:
        source = f.read()
    tree = parser.parse(source)

    module_id = _symbol_id(file_path, "module", "")
    symbols = [
        ParsedSymbol(
            id=module_id, kind="module", name=file_path, file_path=file_path,
            start_line=1, end_line=source.count(b"\n") + 1, language=config.parser_name,
            content_hash=_content_hash(source),
        )
    ]
    resolved_defines: list[tuple[str, str]] = []
    pending_calls: list[tuple[str, str]] = []
    pending_imports: list[tuple[str, str]] = []
    pending_inherits: list[tuple[str, str]] = []

    def walk(node, enclosing_id: str, enclosing_qualified_name: str):
        if node.type in config.definition_types:
            name = _node_name(node, source)
            if name is not None:
                kind = config.definition_types[node.type]
                qualified_name = f"{enclosing_qualified_name}.{name}" if enclosing_qualified_name else name
                symbol_id = _symbol_id(file_path, kind, qualified_name)
                symbols.append(
                    ParsedSymbol(
                        id=symbol_id, kind=kind, name=name,
                        file_path=file_path, start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1, language=config.parser_name,
                        content_hash=_content_hash(source[node.start_byte:node.end_byte]),
                    )
                )
                resolved_defines.append((enclosing_id, symbol_id))
                for superclass_name in _superclass_names(node, config, source):
                    pending_inherits.append((symbol_id, superclass_name))
                enclosing_id = symbol_id
                enclosing_qualified_name = qualified_name
        elif node.type in config.import_types:
            for imported_name in _import_names(node, source):
                pending_imports.append((enclosing_id, imported_name))
        elif node.type in config.call_types:
            callee_name = _call_target_name(node, source)
            if callee_name:
                pending_calls.append((enclosing_id, callee_name))

        for child in node.children:
            walk(child, enclosing_id, enclosing_qualified_name)

    walk(tree.root_node, module_id, "")
    return symbols, resolved_defines, pending_calls, pending_imports, pending_inherits


def parse_files(file_paths: list[str]):
    """Parse a specific set of files (not a full tree walk) — the incremental-reindex
    entry point: only files that changed need re-parsing. Returns the same shape as
    parse_repo's per-file accumulation, before edge resolution (resolve_edges)."""
    all_symbols: list[ParsedSymbol] = []
    resolved_defines: list[tuple[str, str]] = []
    pending_calls: list[tuple[str, str]] = []
    pending_imports: list[tuple[str, str]] = []
    pending_inherits: list[tuple[str, str]] = []
    for file_path in file_paths:
        ext = os.path.splitext(file_path)[1]
        if ext not in LANGUAGE_CONFIGS:
            continue
        symbols, defines, calls, imports, inherits = _parse_file(file_path, ext)
        all_symbols.extend(symbols)
        resolved_defines.extend(defines)
        pending_calls.extend(calls)
        pending_imports.extend(imports)
        pending_inherits.extend(inherits)
    return all_symbols, resolved_defines, pending_calls, pending_imports, pending_inherits


def resolve_edges(
    index_symbols: list[dict],
    resolved_defines: list[tuple[str, str]],
    pending_calls: list[tuple[str, str]],
    pending_imports: list[tuple[str, str]],
    pending_inherits: list[tuple[str, str]],
) -> list[ParsedEdge]:
    """Resolve DEFINES/CALLS/IMPORTS/INHERITS against a name index built from
    `index_symbols` (plain dicts with at least id/name/kind/file_path — either
    freshly-parsed ParsedSymbol-shaped dicts, or existing GraphStore.get_subgraph()
    node dicts for files that weren't re-parsed this pass)."""
    name_to_id: dict[str, str] = {}
    class_name_to_id: dict[str, str] = {}
    basename_to_module_id: dict[str, str] = {}
    for s in index_symbols:
        name_to_id.setdefault(s["name"], s["id"])
        if s["kind"] == "class":
            class_name_to_id.setdefault(s["name"], s["id"])
        if s["kind"] == "module":
            base = os.path.splitext(os.path.basename(s["file_path"]))[0]
            basename_to_module_id.setdefault(base, s["id"])

    edges = [ParsedEdge(source=p, target=c, type="DEFINES") for p, c in resolved_defines]
    edges += [
        ParsedEdge(source=caller_id, target=name_to_id[callee_name], type="CALLS")
        for caller_id, callee_name in pending_calls
        if callee_name in name_to_id
    ]
    edges += [
        ParsedEdge(source=importer_id, target=basename_to_module_id[imported_name.rsplit(".", 1)[-1]], type="IMPORTS")
        for importer_id, imported_name in pending_imports
        if imported_name.rsplit(".", 1)[-1] in basename_to_module_id
    ]
    edges += [
        ParsedEdge(source=class_id, target=class_name_to_id[superclass_name], type="INHERITS")
        for class_id, superclass_name in pending_inherits
        if superclass_name in class_name_to_id
    ]
    return edges


def _as_index_dicts(symbols: list[ParsedSymbol]) -> list[dict]:
    return [{"id": s.id, "name": s.name, "kind": s.kind, "file_path": s.file_path} for s in symbols]


def parse_repo(root_path: str) -> tuple[list[ParsedSymbol], list[ParsedEdge]]:
    """Walk `root_path`, parse every recognized file, and resolve
    CALLS/IMPORTS/INHERITS edges against a repo-wide name index built after
    all files are parsed."""
    file_paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        for filename in filenames:
            file_paths.append(os.path.join(dirpath, filename))

    all_symbols, resolved_defines, pending_calls, pending_imports, pending_inherits = parse_files(file_paths)
    edges = resolve_edges(
        _as_index_dicts(all_symbols), resolved_defines, pending_calls, pending_imports, pending_inherits
    )
    return all_symbols, edges
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_code_parser.py -v`
Expected: all tests PASS (the 5 pre-existing tests must still pass unmodified — they only assert on symbol/edge *structure*, never a literal id value).

- [ ] **Step 5: Commit**

```bash
git add app/graph/code_parser.py tests/test_code_parser.py
git commit -m "$(cat <<'EOF'
feat(graph): make symbol ids deterministic, add content_hash

Symbol id was uuid4() (random per parse); switched to a hash of
file_path+kind+qualified_name so unchanged symbols keep the same id
across reindexes. content_hash (hash of the symbol's own source
bytes) is the separate signal for whether its content actually
changed. Both are prerequisites for incremental reindex (next tasks)
-- without a stable id there's no way to know "this symbol didn't
change, skip re-embedding it".

Also factors parse_repo's per-file loop into parse_files() and its
index-build+resolve tail into resolve_edges(), both reused by the
incremental reindex path added in a later task.
EOF
)"
```

---

## Task 2: Deterministic Qdrant point id (`qdrant_store.py`)

**Files:**
- Modify: `app/clients/qdrant_store.py`
- Test: `tests/test_qdrant_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_qdrant_store.py`:

```python
def test_symbol_point_id_is_deterministic():
    from app.clients.qdrant_store import symbol_point_id

    assert symbol_point_id("abc123") == symbol_point_id("abc123")


def test_symbol_point_id_differs_for_different_symbol_ids():
    from app.clients.qdrant_store import symbol_point_id

    assert symbol_point_id("abc123") != symbol_point_id("def456")


def test_symbol_point_id_is_a_valid_uuid_string():
    import uuid

    from app.clients.qdrant_store import symbol_point_id

    uuid.UUID(symbol_point_id("abc123"))  # raises ValueError if not a valid UUID string
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_qdrant_store.py -k symbol_point_id -v`
Expected: FAIL with `ImportError: cannot import name 'symbol_point_id'`.

- [ ] **Step 3: Implement `symbol_point_id`**

In `app/clients/qdrant_store.py`, add near the top (after the collection-name constants, before `bootstrap_collections`):

```python
import uuid

# Fixed, arbitrary namespace for deriving Qdrant point ids from stable symbol ids
# (see code_parser.py's _symbol_id) via uuid5 — Qdrant only accepts u64 ints or
# UUIDs as point ids (confirmed against Qdrant docs), not arbitrary strings, so
# the symbol's own sha1-hex id can't be used directly.
_SYMBOL_POINT_NAMESPACE = uuid.UUID("6f6e6f74-6f68-7274-7267-617068747200")


def symbol_point_id(symbol_id: str) -> str:
    return str(uuid.uuid5(_SYMBOL_POINT_NAMESPACE, symbol_id))
```

(`import uuid` goes with the other stdlib imports at the top of the file, not inline — shown inline above only to mark where it's new.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_qdrant_store.py -v`
Expected: all PASS, including the pre-existing tests in this file.

- [ ] **Step 5: Commit**

```bash
git add app/clients/qdrant_store.py tests/test_qdrant_store.py
git commit -m "$(cat <<'EOF'
feat(qdrant): add deterministic point id from stable symbol id

Qdrant only accepts u64 ints or UUIDs as point ids, not arbitrary
strings, so a symbol's sha1-hex id (code_parser.py) can't be used
directly. uuid5(fixed namespace, symbol_id) is deterministic and
UUID-valid -- same symbol always maps to the same point, which
incremental reindex needs to upsert/skip/delete points precisely
instead of wiping a whole repo's vectors on every change.
EOF
)"
```

---

## Task 3: `content_hash` column + `replace_files_in_repo` abstract method

**Files:**
- Modify: `app/graph/code_graph_store.py`

- [ ] **Step 1: Add the abstract method to `GraphStore`**

In `app/graph/code_graph_store.py`, find:

```python
    @abstractmethod
    def replace_repo_graph(self, repo: dict, symbols: list[dict], edges: list[dict]) -> None:
        """Atomically replace a repo's entire code graph (old symbols/edges deleted, new
        repo/symbols/edges written) as a single unit -- a reader must never observe a
        partial state (e.g. all-symbols-no-edges) mid-replace."""
        ...
```

Add immediately after it:

```python
    @abstractmethod
    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        """Incremental variant of replace_repo_graph: only symbols/edges whose file_path is
        in `stale_file_paths` are deleted before `symbols`/`edges` are inserted -- every
        other row already stored for this repo is left untouched. Same no-partial-state
        atomicity contract as replace_repo_graph, scoped to `stale_file_paths` instead of
        the whole repo."""
        ...
```

- [ ] **Step 2: Add `content_hash` to the SQLite schema (with migration for existing dbs)**

In `SqliteGraphStore._init_schema`, change the `code_symbols` table definition and add a guarded `ALTER TABLE` right after the `executescript` call:

```python
    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repos (
                    user_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    source TEXT,
                    local_path TEXT,
                    last_indexed_at TEXT,
                    PRIMARY KEY (user_id, repo_id)
                );
                CREATE TABLE IF NOT EXISTS code_symbols (
                    id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    kind TEXT,
                    name TEXT,
                    file_path TEXT,
                    start_line INTEGER,
                    end_line INTEGER,
                    language TEXT,
                    content_hash TEXT
                );
                CREATE INDEX IF NOT EXISTS code_symbols_scope_idx ON code_symbols (user_id, repo_id);
                CREATE INDEX IF NOT EXISTS code_symbols_file_idx ON code_symbols (user_id, repo_id, file_path);
                CREATE TABLE IF NOT EXISTS code_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    type TEXT NOT NULL,
                    UNIQUE (source, target, type)
                );
                CREATE TABLE IF NOT EXISTS text_entities (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT,
                    entity_type TEXT,
                    source_doc_id TEXT,
                    source_memory_id TEXT
                );
                CREATE INDEX IF NOT EXISTS text_entities_user_idx ON text_entities (user_id);
                CREATE TABLE IF NOT EXISTS related_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    UNIQUE (source, target)
                );
                CREATE TABLE IF NOT EXISTS mentions_edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    UNIQUE (source, target)
                );
                CREATE INDEX IF NOT EXISTS mentions_edges_target_idx ON mentions_edges (target);
                """
            )
            # A db created before content_hash existed has the table but not the column
            # (CREATE TABLE IF NOT EXISTS above is then a no-op) -- sqlite has no
            # `ADD COLUMN IF NOT EXISTS`, so add it and swallow the "already exists"
            # error for dbs created fresh (which already have it from the CREATE TABLE).
            try:
                self._conn.execute("ALTER TABLE code_symbols ADD COLUMN content_hash TEXT")
            except sqlite3.OperationalError:
                pass
```

(The new `code_symbols_file_idx` index isn't strictly required for correctness, but `replace_files_in_repo` (Task 4) filters `code_symbols` by `user_id, repo_id, file_path` on every incremental reindex — worth indexing since that's now a hot path.)

- [ ] **Step 3: Include `content_hash` in symbol upsert/select**

In `_upsert_symbols_unlocked`, normalize missing `content_hash` to `None` (many existing tests build symbol dicts without it — this must not break them) and include it in the INSERT:

```python
    def _upsert_symbols_unlocked(self, symbols: list[dict]) -> None:
        if not symbols:
            return
        rows = [{**s, "content_hash": s.get("content_hash")} for s in symbols]
        self._conn.executemany(
            """
            INSERT INTO code_symbols
                (id, repo_id, user_id, kind, name, file_path, start_line, end_line, language, content_hash)
            VALUES
                (:id, :repo_id, :user_id, :kind, :name, :file_path, :start_line, :end_line, :language, :content_hash)
            ON CONFLICT (id) DO UPDATE SET
                repo_id = excluded.repo_id, user_id = excluded.user_id, kind = excluded.kind,
                name = excluded.name, file_path = excluded.file_path, start_line = excluded.start_line,
                end_line = excluded.end_line, language = excluded.language, content_hash = excluded.content_hash
            """,
            rows,
        )
```

In `get_subgraph`, add `content_hash` to the SELECT column list:

```python
            symbol_rows = self._conn.execute(
                "SELECT id, repo_id, user_id, kind, name, file_path, start_line, end_line, language, content_hash "
                "FROM code_symbols WHERE user_id = ? AND repo_id = ?",
                (user_id, repo_id),
            ).fetchall()
```

- [ ] **Step 4: Add `content_hash` to Neo4j's symbol-writing Cypher**

`Neo4jGraphStore` has three places that `SET` CodeSymbol properties from a symbol dict — `upsert_symbols`, the symbols block inside `replace_repo_graph`, and (Task 5) the new `replace_files_in_repo`. Add `n.content_hash = s.content_hash` to the `SET` clause in `upsert_symbols`:

```python
    def upsert_symbols(self, symbols: list[dict]) -> None:
        if not symbols:
            return
        self._driver.execute_query(
            """
            UNWIND $symbols AS s
            MERGE (n:CodeSymbol {id: s.id})
            SET n.repo_id = s.repo_id, n.user_id = s.user_id, n.kind = s.kind,
                n.name = s.name, n.file_path = s.file_path, n.start_line = s.start_line,
                n.end_line = s.end_line, n.language = s.language, n.content_hash = s.content_hash
            """,
            symbols=symbols,
        )
```

and in `replace_repo_graph`'s inner symbols block:

```python
            if symbols:
                tx.run(
                    """
                    UNWIND $symbols AS s
                    MERGE (n:CodeSymbol {id: s.id})
                    SET n.repo_id = s.repo_id, n.user_id = s.user_id, n.kind = s.kind,
                        n.name = s.name, n.file_path = s.file_path, n.start_line = s.start_line,
                        n.end_line = s.end_line, n.language = s.language, n.content_hash = s.content_hash
                    """,
                    symbols=symbols,
                )
```

(A dict missing `content_hash` reads as Cypher `null` for `s.content_hash`, and `SET n.content_hash = null` just removes/never-sets that property — no error, existing callers that don't pass `content_hash` keep working exactly as before.)

- [ ] **Step 5: No test run yet** — this task only lays groundwork consumed by Tasks 4/5/6. Verification happens in those tasks' test runs (a bare schema/signature change with no implementing subclass would fail to import, since `GraphStore` is now missing a concrete `replace_files_in_repo` on 4 subclasses — that's expected and fixed by the next three tasks, not this one).

- [ ] **Step 6: Commit**

```bash
git add app/graph/code_graph_store.py
git commit -m "$(cat <<'EOF'
feat(graph): add content_hash column + replace_files_in_repo signature

Groundwork for incremental reindex: content_hash flows through
Sqlite's code_symbols table (migrated via guarded ALTER TABLE for
pre-existing dbs) and Neo4j's CodeSymbol nodes, and GraphStore gains
the abstract replace_files_in_repo method that Sqlite/Neo4j/
LocalMultiRepo/Fake implementations fill in next.
EOF
)"
```

---

## Task 4: `SqliteGraphStore.replace_files_in_repo`

**Files:**
- Modify: `app/graph/code_graph_store.py`
- Test: `tests/test_sqlite_graph_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sqlite_graph_store.py`:

```python
def test_sqlite_replace_files_in_repo_only_touches_symbols_in_stale_file_paths(sqlite_store):
    sqlite_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                               "local_path": "/tmp/r1", "last_indexed_at": "t0"})
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "hash-a"},
        {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python",
         "content_hash": "hash-b"},
    ])
    sqlite_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    sqlite_store.replace_files_in_repo(
        {"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1", "local_path": "/tmp/r1", "last_indexed_at": "t1"},
        ["a.py"],
        [{"id": "a2", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo_renamed",
          "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python",
          "content_hash": "hash-a2"}],
        [],
    )

    nodes, edges = sqlite_store.get_subgraph("u1", "r1")
    by_name = {n["name"]: n for n in nodes}
    assert "foo" not in by_name  # old a.py symbol gone
    assert by_name["foo_renamed"]["content_hash"] == "hash-a2"
    assert by_name["bar"]["content_hash"] == "hash-b"  # untouched b.py symbol keeps its content_hash
    assert edges == []  # the CALLS edge referencing the deleted "a" id is gone too
    assert sqlite_store.get_repo("u1", "r1")["last_indexed_at"] == "t1"


def test_sqlite_replace_files_in_repo_with_no_symbols_just_deletes_stale_files(sqlite_store):
    sqlite_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                               "local_path": "/tmp/r1", "last_indexed_at": "t0"})
    sqlite_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "h"},
    ])

    sqlite_store.replace_files_in_repo(
        {"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1", "local_path": "/tmp/r1", "last_indexed_at": "t1"},
        ["a.py"], [], [],
    )

    nodes, _ = sqlite_store.get_subgraph("u1", "r1")
    assert nodes == []


def test_sqlite_content_hash_column_migrates_on_pre_existing_db(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "old.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE code_symbols (id TEXT PRIMARY KEY, repo_id TEXT, user_id TEXT, kind TEXT, "
        "name TEXT, file_path TEXT, start_line INTEGER, end_line INTEGER, language TEXT)"
    )
    conn.commit()
    conn.close()

    store = SqliteGraphStore(db_path)  # must not raise

    store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "h"},
    ])
    nodes, _ = store.get_subgraph("u1", "r1")
    assert nodes[0]["content_hash"] == "h"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_sqlite_graph_store.py -k "replace_files_in_repo or migrates" -v`
Expected: first two FAIL with `AttributeError: 'SqliteGraphStore' object has no attribute 'replace_files_in_repo'` (and/or `TypeError: Can't instantiate abstract class SqliteGraphStore` if Task 3 already added the abstract method without an implementation — same root cause). Third test currently PASSES already (migration logic was added in Task 3) — that's fine, it's here for completeness/regression coverage alongside the new feature tests, not strictly new-red.

- [ ] **Step 3: Implement `replace_files_in_repo`**

In `app/graph/code_graph_store.py`, add a new unlocked helper right after `_delete_repo_unlocked`, then the public method after `replace_repo_graph`:

```python
    def _delete_files_unlocked(self, user_id: str, repo_id: str, file_paths: list[str]) -> None:
        if not file_paths:
            return
        file_placeholders = _in_clause(len(file_paths))
        ids = [
            row["id"] for row in self._conn.execute(
                f"SELECT id FROM code_symbols WHERE user_id = ? AND repo_id = ? AND file_path IN {file_placeholders}",
                [user_id, repo_id] + file_paths,
            ).fetchall()
        ]
        if not ids:
            return
        id_placeholders = _in_clause(len(ids))
        self._conn.execute(f"DELETE FROM code_symbols WHERE id IN {id_placeholders}", ids)
        self._conn.execute(
            f"DELETE FROM code_edges WHERE source IN {id_placeholders} OR target IN {id_placeholders}", ids + ids
        )
        self._conn.execute(f"DELETE FROM mentions_edges WHERE target IN {id_placeholders}", ids)
```

```python
    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        with self._lock, self._conn:
            self._delete_files_unlocked(repo["user_id"], repo["repo_id"], stale_file_paths)
            self._upsert_repo_unlocked(repo)
            self._upsert_symbols_unlocked(symbols)
            self._upsert_code_edges_unlocked(edges)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_sqlite_graph_store.py -v`
Expected: all PASS, including every pre-existing test in the file.

- [ ] **Step 5: Commit**

```bash
git add app/graph/code_graph_store.py tests/test_sqlite_graph_store.py
git commit -m "$(cat <<'EOF'
feat(graph): implement replace_files_in_repo on SqliteGraphStore

Deletes only symbols/edges/mentions whose file_path is in the given
list, then inserts the new set -- every other row for the repo is
untouched. Mirrors replace_repo_graph's structure, scoped to files
instead of the whole repo.
EOF
)"
```

---

## Task 5: `Neo4jGraphStore.replace_files_in_repo`

**Files:**
- Modify: `app/graph/code_graph_store.py`
- Test: `tests/test_code_graph_store_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_code_graph_store_integration.py`:

```python
def test_replace_files_in_repo_only_touches_symbols_in_stale_file_paths_through_real_neo4j(neo4j_store):
    neo4j_store.replace_repo_graph(
        {"user_id": "test-u1", "repo_id": "test-r1", "source": "x", "local_path": "x", "last_indexed_at": "t0"},
        [
            {"id": "int-a", "user_id": "test-u1", "repo_id": "test-r1", "kind": "function", "name": "foo",
             "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "hash-a"},
            {"id": "int-b", "user_id": "test-u1", "repo_id": "test-r1", "kind": "function", "name": "bar",
             "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "hash-b"},
        ],
        [{"source": "int-a", "target": "int-b", "type": "CALLS"}],
    )

    neo4j_store.replace_files_in_repo(
        {"user_id": "test-u1", "repo_id": "test-r1", "source": "x", "local_path": "x", "last_indexed_at": "t1"},
        ["a.py"],
        [{"id": "int-a2", "user_id": "test-u1", "repo_id": "test-r1", "kind": "function", "name": "foo_renamed",
          "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "hash-a2"}],
        [],
    )

    nodes, edges = neo4j_store.get_subgraph("test-u1", "test-r1")
    by_name = {n["name"]: n for n in nodes}
    assert "foo" not in by_name
    assert by_name["foo_renamed"]["content_hash"] == "hash-a2"
    assert by_name["bar"]["content_hash"] == "hash-b"
    assert edges == []
```

- [ ] **Step 2: Run test to verify it fails (or skips)**

Run: `NEO4J_TEST_URL=bolt://localhost:7687 NEO4J_TEST_PASSWORD=<pw> .venv/bin/python -m pytest tests/test_code_graph_store_integration.py -k replace_files_in_repo -v`

If no live Neo4j is available, this test SKIPs (`pytestmark = pytest.mark.skipif(not NEO4J_TEST_URL, ...)`) — that's fine, don't block on provisioning Neo4j just for this task; Task 4/6's Sqlite/Fake coverage plus this test compiling correctly (no `AttributeError` at collection time) is enough signal without a live instance. If a live instance is available, expect FAIL with `AttributeError: 'Neo4jGraphStore' object has no attribute 'replace_files_in_repo'`.

- [ ] **Step 3: Implement `replace_files_in_repo`**

In `app/graph/code_graph_store.py`, add to `Neo4jGraphStore` right after `replace_repo_graph`:

```python
    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        user_id, repo_id = repo["user_id"], repo["repo_id"]

        def _work(tx):
            if stale_file_paths:
                tx.run(
                    "MATCH (n:CodeSymbol {user_id: $user_id, repo_id: $repo_id}) "
                    "WHERE n.file_path IN $file_paths DETACH DELETE n",
                    user_id=user_id, repo_id=repo_id, file_paths=stale_file_paths,
                )
            tx.run(
                """
                MERGE (r:Repo {repo_id: $repo_id, user_id: $user_id})
                SET r.source = $source, r.local_path = $local_path, r.last_indexed_at = $last_indexed_at
                """,
                **repo,
            )
            if symbols:
                tx.run(
                    """
                    UNWIND $symbols AS s
                    MERGE (n:CodeSymbol {id: s.id})
                    SET n.repo_id = s.repo_id, n.user_id = s.user_id, n.kind = s.kind,
                        n.name = s.name, n.file_path = s.file_path, n.start_line = s.start_line,
                        n.end_line = s.end_line, n.language = s.language, n.content_hash = s.content_hash
                    """,
                    symbols=symbols,
                )

            by_type: dict[str, list[dict]] = {}
            for e in edges:
                if e["type"] not in _CODE_EDGE_TYPES:
                    raise ValueError(f"unknown code edge type: {e['type']}")
                by_type.setdefault(e["type"], []).append({"source": e["source"], "target": e["target"]})

            for edge_type, batch in by_type.items():
                tx.run(
                    f"""
                    UNWIND $batch AS e
                    MATCH (a:CodeSymbol {{id: e.source}}), (b:CodeSymbol {{id: e.target}})
                    MERGE (a)-[:{edge_type}]->(b)
                    """,
                    batch=batch,
                )

        with self._driver.session() as session:
            session.execute_write(_work)
```

- [ ] **Step 4: Run test to verify it passes (or still skips cleanly)**

Run: same command as Step 2.
Expected: PASS if Neo4j is available; SKIP (not FAIL/ERROR) otherwise.

- [ ] **Step 5: Commit**

```bash
git add app/graph/code_graph_store.py tests/test_code_graph_store_integration.py
git commit -m "$(cat <<'EOF'
feat(graph): implement replace_files_in_repo on Neo4jGraphStore

Same scoped-delete-then-insert contract as the Sqlite implementation:
only CodeSymbol nodes whose file_path is in the given list are
detached-deleted before the new set is merged in.
EOF
)"
```

---

## Task 6: `FakeGraphStore` + `LocalMultiRepoGraphStore.replace_files_in_repo`

**Files:**
- Modify: `tests/conftest.py`
- Modify: `app/graph/code_graph_store.py`
- Test: `tests/test_code_graph_store.py`, `tests/test_local_multi_repo_graph_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_code_graph_store.py`:

```python
def test_replace_files_in_repo_only_touches_symbols_in_stale_file_paths(graph_store):
    graph_store.upsert_repo({"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1",
                              "local_path": "/tmp/r1", "last_indexed_at": "t0"})
    graph_store.upsert_symbols([
        {"id": "a", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo",
         "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "hash-a"},
        {"id": "b", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "bar",
         "file_path": "b.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "hash-b"},
    ])
    graph_store.upsert_code_edges([{"source": "a", "target": "b", "type": "CALLS"}])

    graph_store.replace_files_in_repo(
        {"user_id": "u1", "repo_id": "r1", "source": "/tmp/r1", "local_path": "/tmp/r1", "last_indexed_at": "t1"},
        ["a.py"],
        [{"id": "a2", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo_renamed",
          "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "hash-a2"}],
        [],
    )

    nodes, edges = graph_store.get_subgraph("u1", "r1")
    by_name = {n["name"]: n for n in nodes}
    assert "foo" not in by_name
    assert by_name["foo_renamed"]["content_hash"] == "hash-a2"
    assert by_name["bar"]["content_hash"] == "hash-b"
    assert edges == []
```

Append to `tests/test_local_multi_repo_graph_store.py`:

```python
def test_replace_files_in_repo_routes_to_the_right_repo_store_and_leaves_others_untouched(tmp_path, store):
    repo1 = _repo_dir(tmp_path, "repo1")
    repo2 = _repo_dir(tmp_path, "repo2")
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r1", "source": repo1, "local_path": repo1, "last_indexed_at": "t0"},
        [_symbol("r1", "a", "foo")], [],
    )
    store.replace_repo_graph(
        {"user_id": "u1", "repo_id": "r2", "source": repo2, "local_path": repo2, "last_indexed_at": "t0"},
        [_symbol("r2", "b", "bar")], [],
    )

    store.replace_files_in_repo(
        {"user_id": "u1", "repo_id": "r1", "source": repo1, "local_path": repo1, "last_indexed_at": "t1"},
        ["a.py"],
        [{"id": "a2", "user_id": "u1", "repo_id": "r1", "kind": "function", "name": "foo_renamed",
          "file_path": "a.py", "start_line": 1, "end_line": 2, "language": "python", "content_hash": "h"}],
        [],
    )

    r1_nodes, _ = store.get_subgraph("u1", "r1")
    assert [n["name"] for n in r1_nodes] == ["foo_renamed"]
    r2_nodes, _ = store.get_subgraph("u1", "r2")
    assert [n["name"] for n in r2_nodes] == ["bar"]  # repo2 untouched
```

(`_symbol()`/`_repo_dir()` helpers already exist in `tests/test_local_multi_repo_graph_store.py` from the earlier per-repo-storage work — reuse them, don't redefine.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_code_graph_store.py tests/test_local_multi_repo_graph_store.py -k replace_files_in_repo -v`
Expected: FAIL with `AttributeError` (or `TypeError: Can't instantiate abstract class FakeGraphStore` at fixture setup, same root cause) on both.

- [ ] **Step 3: Implement on `FakeGraphStore`**

In `tests/conftest.py`, add to `FakeGraphStore` right after `replace_repo_graph`:

```python
    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        stale_ids = {
            sid for sid, s in self.symbols.items()
            if s["user_id"] == repo["user_id"] and s["repo_id"] == repo["repo_id"]
            and s["file_path"] in stale_file_paths
        }
        for sid in stale_ids:
            del self.symbols[sid]
        self.code_edges = [
            e for e in self.code_edges if e["source"] not in stale_ids and e["target"] not in stale_ids
        ]
        self.mentions_edges = [e for e in self.mentions_edges if e["target"] not in stale_ids]
        self.upsert_repo(repo)
        self.upsert_symbols(symbols)
        self.upsert_code_edges(edges)
```

- [ ] **Step 4: Implement on `LocalMultiRepoGraphStore`**

In `app/graph/code_graph_store.py`, add right after `LocalMultiRepoGraphStore.replace_repo_graph`:

```python
    def replace_files_in_repo(
        self, repo: dict, stale_file_paths: list[str], symbols: list[dict], edges: list[dict]
    ) -> None:
        self._central.upsert_repo(repo)
        self._repo_store(repo["local_path"]).replace_files_in_repo(repo, stale_file_paths, symbols, edges)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_code_graph_store.py tests/test_local_multi_repo_graph_store.py -v`
Expected: all PASS, including every pre-existing test in both files.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py app/graph/code_graph_store.py tests/test_code_graph_store.py tests/test_local_multi_repo_graph_store.py
git commit -m "$(cat <<'EOF'
feat(graph): implement replace_files_in_repo on FakeGraphStore, LocalMultiRepoGraphStore

Completes GraphStore.replace_files_in_repo across all four
implementations (Sqlite, Neo4j, Fake test double, and today's
per-repo router) -- the interface is now fully usable by
repo_watcher's incremental reindex path (next task).
EOF
)"
```

---

## Task 7: Watcher batches changed/deleted paths; `reindex_paths` orchestration

**Files:**
- Modify: `app/graph/repo_watcher.py`
- Test: `tests/test_repo_watcher.py`

This is the core orchestration task. It's split into three sub-steps (handler batching, then `reindex_paths`, then embedding) because each is independently testable, but they land in one commit since `reindex_paths` doesn't work without the batching change and vice versa.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repo_watcher.py`:

```python
def test_reindex_paths_only_reembeds_the_changed_file(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))  # full baseline ingest
    nodes_before, _ = graph_store.get_subgraph("user-1", "repo-1")
    bar_before = next(n for n in nodes_before if n["name"] == "bar")

    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")  # only a.py changes

    embed_calls = []
    real_embed_batch = manager._embedder.embed_batch
    manager._embedder.embed_batch = lambda texts: (embed_calls.append(texts) or real_embed_batch(texts))

    manager.reindex_paths("user-1", "repo-1", str(tmp_path), {str(tmp_path / "a.py")}, set())

    nodes_after, _ = graph_store.get_subgraph("user-1", "repo-1")
    bar_after = next(n for n in nodes_after if n["name"] == "bar")
    assert bar_after == bar_before  # untouched file's symbol: same id, same content_hash
    # exactly one embed_batch call, covering only a.py's symbols (module + foo), not b.py's
    assert len(embed_calls) == 1
    assert not any("bar" in t for t in embed_calls[0])


def test_reindex_paths_removes_symbols_and_vectors_for_a_deleted_file(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))
    (tmp_path / "b.py").unlink()

    manager.reindex_paths("user-1", "repo-1", str(tmp_path), set(), {str(tmp_path / "b.py")})

    nodes, _ = graph_store.get_subgraph("user-1", "repo-1")
    assert "bar" not in {n["name"] for n in nodes}
    assert "foo" in {n["name"] for n in nodes}  # a.py untouched


def test_reindex_paths_resolves_a_call_from_a_changed_file_into_an_unchanged_files_symbol(tmp_path, graph_store, qdrant):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))

    (tmp_path / "a.py").write_text("def foo():\n    bar()\n")  # a.py now calls bar() from unchanged b.py

    manager.reindex_paths("user-1", "repo-1", str(tmp_path), {str(tmp_path / "a.py")}, set())

    nodes, edges = graph_store.get_subgraph("user-1", "repo-1")
    by_name = {n["name"]: n["id"] for n in nodes}
    calls = [e for e in edges if e["type"] == "CALLS"]
    assert any(e["source"] == by_name["foo"] and e["target"] == by_name["bar"] for e in calls)


def test_reindex_paths_drops_rather_than_reresolves_an_edge_from_an_unchanged_file_after_a_rename(
    tmp_path, graph_store, qdrant,
):
    """Documents the accepted edge-staleness policy (design spec section 'Edge staleness'):
    when file A renames a symbol that file B (unchanged) calls, the old CALLS edge is
    cleanly removed -- its target id no longer exists, and replace_files_in_repo's
    cascade delete (any edge touching a deleted symbol id) takes it out along with
    foo's old row, so it never dangles pointing at a nonexistent id. But since B isn't
    reparsed this pass, no *new* edge to foo_renamed is created either -- the CALLS
    relationship simply disappears from the graph until B itself is reindexed. This is
    intentional (not a bug): safe (no broken reference) but temporarily incomplete."""
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def use_it():\n    foo()\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder())
    manager.reindex("user-1", "repo-1", str(tmp_path))
    nodes, edges = graph_store.get_subgraph("user-1", "repo-1")
    old_foo_id = next(n["id"] for n in nodes if n["name"] == "foo")
    assert any(e["target"] == old_foo_id and e["type"] == "CALLS" for e in edges)

    (tmp_path / "a.py").write_text("def foo_renamed():\n    pass\n")  # rename, b.py untouched
    manager.reindex_paths("user-1", "repo-1", str(tmp_path), {str(tmp_path / "a.py")}, set())

    nodes, edges = graph_store.get_subgraph("user-1", "repo-1")
    calls = [e for e in edges if e["type"] == "CALLS"]
    assert calls == []  # old edge cascade-deleted with foo's old row, no new edge in its place
    assert "foo_renamed" in {n["name"] for n in nodes}  # the renamed symbol itself exists fine
    assert "use_it" in {n["name"] for n in nodes}  # b.py's own symbol untouched


def test_change_handler_buckets_create_modify_delete_and_move_events(tmp_path):
    from watchdog.events import FileCreatedEvent, FileDeletedEvent, FileModifiedEvent, FileMovedEvent

    from app.graph.repo_watcher import _PendingChanges, _RepoChangeHandler

    pending = _PendingChanges()
    fired = []
    handler = _RepoChangeHandler(pending, lambda: fired.append(1), debounce_seconds=0.01)

    a = str(tmp_path / "a.py")
    b = str(tmp_path / "b.py")
    c = str(tmp_path / "c.py")

    handler.on_created(FileCreatedEvent(a))
    handler.on_modified(FileModifiedEvent(a))  # same file twice -> one entry
    handler.on_deleted(FileDeletedEvent(b))
    handler.on_moved(FileMovedEvent(c, str(tmp_path / "c2.py")))

    assert pending.changed == {a, str(tmp_path / "c2.py")}
    assert pending.deleted == {b, c}
    handler.cancel()


def test_change_handler_modify_then_delete_same_file_ends_up_deleted_only(tmp_path):
    from watchdog.events import FileDeletedEvent, FileModifiedEvent

    from app.graph.repo_watcher import _PendingChanges, _RepoChangeHandler

    pending = _PendingChanges()
    handler = _RepoChangeHandler(pending, lambda: None, debounce_seconds=0.01)
    a = str(tmp_path / "a.py")

    handler.on_modified(FileModifiedEvent(a))
    handler.on_deleted(FileDeletedEvent(a))

    assert pending.changed == set()
    assert pending.deleted == {a}
    handler.cancel()


def test_watch_reindexes_only_the_saved_file_end_to_end(tmp_path, graph_store, qdrant):
    """End-to-end through the real watchdog Observer (like the existing
    test_watch_reindexes_automatically_on_file_change), proving the batching
    handler + reindex_paths wiring in watch() actually works together, not
    just each piece in isolation."""
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")
    manager = RepoWatcherManager(graph_store, qdrant_client=qdrant, embedder=_FakeEmbedder(), debounce_seconds=0.2)
    manager.reindex("user-1", "repo-1", str(tmp_path))
    nodes_before, _ = graph_store.get_subgraph("user-1", "repo-1")
    bar_id_before = next(n["id"] for n in nodes_before if n["name"] == "bar")

    manager.watch("user-1", "repo-1", str(tmp_path))
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")

    deadline = time.time() + 5
    found = False
    while time.time() < deadline:
        nodes, _ = graph_store.get_subgraph("user-1", "repo-1")
        bar_now = next((n for n in nodes if n["name"] == "bar"), None)
        if bar_now is not None and bar_now["id"] == bar_id_before and bar_now.get("content_hash"):
            found = True
            break
        time.sleep(0.1)

    manager.stop()
    assert found, "watcher did not settle on the expected post-reindex state within the timeout"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_repo_watcher.py -v`
Expected: the new tests FAIL — `reindex_paths`/`_PendingChanges` don't exist yet (`AttributeError`/`ImportError`), `_RepoChangeHandler(pending, ...)` signature doesn't match the current `(on_change, debounce_seconds)` constructor (`TypeError`). Every pre-existing test in the file must still PASS at this point (they exercise `reindex()`/`watch()`/`resume_all()`, none of which are touched until Step 3).

- [ ] **Step 3: Rewrite `repo_watcher.py`**

Replace the whole file with:

```python
"""Background filesystem watcher that keeps each ingested repo's code graph in
sync. File events within one debounce window are batched into changed/deleted
path sets (see _PendingChanges) and reindex_paths() re-parses+re-embeds only
those files, merging them against the existing graph to resolve cross-file
edges -- not a full repo re-parse/re-embed on every save. reindex() (full)
is kept for ingest_codebase's first-time parse and as a manual fallback.
reindex_paths() is serialized per (user_id, repo_id) the same way reindex()
always was, so a slow embed call from one debounced fire can't overlap with
the next."""

import datetime
import os
import threading

from qdrant_client.models import FieldCondition, Filter, MatchValue, PointIdsList, PointStruct
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.graph.code_graph_store import GraphStore
from app.graph.code_parser import LANGUAGE_CONFIGS, _IGNORED_DIRS, parse_files, parse_repo, resolve_edges
from app.clients.qdrant_store import CODE_SYMBOL_EMBEDDINGS, symbol_point_id

_DEFAULT_DEBOUNCE_SECONDS = 2.0


def _is_relevant_change(path: str) -> bool:
    # parse_repo/parse_files only read recognized source extensions and skip
    # _IGNORED_DIRS, so events elsewhere (.git/index churn, __pycache__,
    # node_modules, non-source file saves) can't change the parsed graph --
    # scheduling a reindex for them just burns work for nothing.
    parts = path.split(os.sep)
    if any(part in _IGNORED_DIRS or part.startswith(".") for part in parts[:-1]):
        return False
    return os.path.splitext(path)[1] in LANGUAGE_CONFIGS


class _PendingChanges:
    """Accumulates changed/deleted file paths across one debounce window. A
    path can be marked changed then deleted (or vice versa) before the timer
    fires -- each mark clears the path from the other set, so it only ever
    ends up in the one that reflects its latest known state."""

    def __init__(self):
        self.changed: set[str] = set()
        self.deleted: set[str] = set()

    def mark_changed(self, path: str) -> None:
        self.deleted.discard(path)
        self.changed.add(path)

    def mark_deleted(self, path: str) -> None:
        self.changed.discard(path)
        self.deleted.add(path)

    def take(self) -> tuple[set[str], set[str]]:
        changed, deleted = self.changed, self.deleted
        self.changed, self.deleted = set(), set()
        return changed, deleted


class _RepoChangeHandler(FileSystemEventHandler):
    def __init__(self, pending: _PendingChanges, on_change, debounce_seconds: float):
        self._pending = pending
        self._on_change = on_change
        self._debounce_seconds = debounce_seconds
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._on_change)
            self._timer.daemon = True
            self._timer.start()

    def on_created(self, event) -> None:
        if _is_relevant_change(event.src_path):
            self._pending.mark_changed(event.src_path)
            self._schedule()

    def on_modified(self, event) -> None:
        if _is_relevant_change(event.src_path):
            self._pending.mark_changed(event.src_path)
            self._schedule()

    def on_deleted(self, event) -> None:
        if _is_relevant_change(event.src_path):
            self._pending.mark_deleted(event.src_path)
            self._schedule()

    def on_moved(self, event) -> None:
        scheduled = False
        if _is_relevant_change(event.src_path):
            self._pending.mark_deleted(event.src_path)
            scheduled = True
        if _is_relevant_change(event.dest_path):
            self._pending.mark_changed(event.dest_path)
            scheduled = True
        if scheduled:
            self._schedule()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class RepoWatcherManager:
    def __init__(
        self, graph_store: GraphStore, debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        qdrant_client=None, embedder=None, qdrant_client_resolver=None,
    ):
        self._graph_store = graph_store
        self._debounce_seconds = debounce_seconds
        self._qdrant_client = qdrant_client
        self._embedder = embedder
        # DEPLOY_MODE=local wires this to open a per-repo embedded Qdrant client keyed by
        # local_path (see get_repo_qdrant_client), instead of every repo sharing one
        # collection that grows unbounded. Takes precedence over qdrant_client when set.
        self._qdrant_client_resolver = qdrant_client_resolver
        self._observer: Observer | None = None
        self._watches: dict[tuple[str, str], tuple[object, _RepoChangeHandler]] = {}
        # One lock per (user_id, repo_id) so concurrent reindex triggers for the
        # same repo (e.g. a second debounced fire landing while a slow embed
        # call from the first is still running) serialize instead of racing on
        # the same Qdrant/graph-store writes.
        self._reindex_locks: dict[tuple[str, str], threading.Lock] = {}

    def _ensure_observer(self) -> Observer:
        # Started lazily on first watch() rather than in __init__ so a manager
        # that's constructed but never used to watch a repo (e.g. built up
        # front, watched conditionally) doesn't leak an OS thread.
        if self._observer is None:
            self._observer = Observer()
            self._observer.start()
        return self._observer

    def _resolve_qdrant_client(self, local_path: str):
        return self._qdrant_client_resolver(local_path) if self._qdrant_client_resolver else self._qdrant_client

    def reindex(self, user_id: str, repo_id: str, local_path: str) -> None:
        key = (user_id, repo_id)
        lock = self._reindex_locks.setdefault(key, threading.Lock())
        with lock:
            symbols, edges = parse_repo(local_path)
            self._graph_store.replace_repo_graph(
                {
                    "user_id": user_id, "repo_id": repo_id, "source": local_path,
                    "local_path": local_path, "last_indexed_at": datetime.datetime.utcnow().isoformat(),
                },
                [
                    {"id": s.id, "repo_id": repo_id, "user_id": user_id, "kind": s.kind, "name": s.name,
                     "file_path": s.file_path, "start_line": s.start_line, "end_line": s.end_line,
                     "language": s.language, "content_hash": s.content_hash}
                    for s in symbols
                ],
                [{"source": e.source, "target": e.target, "type": e.type} for e in edges],
            )
            client = self._resolve_qdrant_client(local_path)
            if client is not None and self._embedder is not None:
                self._replace_symbol_embeddings(client, user_id, repo_id, symbols)

    def _replace_symbol_embeddings(self, client, user_id: str, repo_id: str, symbols) -> None:
        # Mirrors replace_repo_graph's atomic-replace semantics: drop this
        # repo's previous vectors before writing the new set, or every full
        # reindex would pile up a fresh duplicate copy on top of the last one
        # forever.
        client.delete(
            collection_name=CODE_SYMBOL_EMBEDDINGS,
            points_selector=Filter(
                must=[
                    FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                    FieldCondition(key="repo_id", match=MatchValue(value=repo_id)),
                ]
            ),
        )
        if not symbols:
            return
        vectors = self._embedder.embed_batch([f"{s.kind} {s.name}" for s in symbols])
        client.upsert(
            collection_name=CODE_SYMBOL_EMBEDDINGS,
            points=[
                PointStruct(
                    id=symbol_point_id(s.id), vector=vector,
                    payload={
                        "symbol_id": s.id, "user_id": user_id, "repo_id": repo_id,
                        "name": s.name, "kind": s.kind, "file_path": s.file_path,
                    },
                )
                for s, vector in zip(symbols, vectors)
            ],
            wait=True,
        )

    def reindex_paths(
        self, user_id: str, repo_id: str, local_path: str, changed_paths: set[str], deleted_paths: set[str],
    ) -> None:
        key = (user_id, repo_id)
        lock = self._reindex_locks.setdefault(key, threading.Lock())
        with lock:
            # A path can be stale by the time the debounce timer fires (e.g.
            # created then deleted within one window) -- if it's not on disk
            # anymore, treat it as deleted rather than trying to parse it.
            existing_paths = {p for p in changed_paths if os.path.isfile(p)}
            deleted_paths = deleted_paths | (changed_paths - existing_paths)

            new_symbols, resolved_defines, pending_calls, pending_imports, pending_inherits = parse_files(
                list(existing_paths)
            )

            baseline_nodes, _ = self._graph_store.get_subgraph(user_id, repo_id)
            # get_subgraph also returns mentioning TextEntity nodes, which have no
            # file_path -- only CodeSymbol-shaped nodes matter for the index/diff below.
            code_baseline_nodes = [n for n in baseline_nodes if "file_path" in n]
            stale_file_paths = existing_paths | deleted_paths
            kept_baseline = [n for n in code_baseline_nodes if n["file_path"] not in stale_file_paths]
            stale_baseline_by_id = {n["id"]: n for n in code_baseline_nodes if n["file_path"] in stale_file_paths}

            index_symbols = kept_baseline + [
                {"id": s.id, "name": s.name, "kind": s.kind, "file_path": s.file_path} for s in new_symbols
            ]
            new_edges = resolve_edges(
                index_symbols, resolved_defines, pending_calls, pending_imports, pending_inherits
            )

            self._graph_store.replace_files_in_repo(
                {
                    "user_id": user_id, "repo_id": repo_id, "source": local_path,
                    "local_path": local_path, "last_indexed_at": datetime.datetime.utcnow().isoformat(),
                },
                list(stale_file_paths),
                [
                    {"id": s.id, "repo_id": repo_id, "user_id": user_id, "kind": s.kind, "name": s.name,
                     "file_path": s.file_path, "start_line": s.start_line, "end_line": s.end_line,
                     "language": s.language, "content_hash": s.content_hash}
                    for s in new_symbols
                ],
                [{"source": e.source, "target": e.target, "type": e.type} for e in new_edges],
            )

            client = self._resolve_qdrant_client(local_path)
            if client is not None and self._embedder is not None:
                self._update_symbol_embeddings(client, user_id, repo_id, new_symbols, stale_baseline_by_id)

    def _update_symbol_embeddings(self, client, user_id: str, repo_id: str, new_symbols, stale_baseline_by_id) -> None:
        new_ids = {s.id for s in new_symbols}
        to_embed = [
            s for s in new_symbols
            if s.id not in stale_baseline_by_id or stale_baseline_by_id[s.id].get("content_hash") != s.content_hash
        ]
        removed_ids = [sid for sid in stale_baseline_by_id if sid not in new_ids]

        if removed_ids:
            client.delete(
                collection_name=CODE_SYMBOL_EMBEDDINGS,
                points_selector=PointIdsList(points=[symbol_point_id(sid) for sid in removed_ids]),
            )
        if not to_embed:
            return
        vectors = self._embedder.embed_batch([f"{s.kind} {s.name}" for s in to_embed])
        client.upsert(
            collection_name=CODE_SYMBOL_EMBEDDINGS,
            points=[
                PointStruct(
                    id=symbol_point_id(s.id), vector=vector,
                    payload={
                        "symbol_id": s.id, "user_id": user_id, "repo_id": repo_id,
                        "name": s.name, "kind": s.kind, "file_path": s.file_path,
                    },
                )
                for s, vector in zip(to_embed, vectors)
            ],
            wait=True,
        )

    def watch(self, user_id: str, repo_id: str, local_path: str) -> None:
        key = (user_id, repo_id)
        if key in self._watches:
            return
        pending = _PendingChanges()

        def on_fire():
            changed, deleted = pending.take()
            self.reindex_paths(user_id, repo_id, local_path, changed, deleted)

        handler = _RepoChangeHandler(pending, on_fire, self._debounce_seconds)
        watch = self._ensure_observer().schedule(handler, local_path, recursive=True)
        self._watches[key] = (watch, handler)

    def unwatch(self, user_id: str, repo_id: str) -> None:
        entry = self._watches.pop((user_id, repo_id), None)
        if entry is not None:
            watch, handler = entry
            handler.cancel()  # drop any debounce timer already scheduled, or it'd still fire after unwatch
            if self._observer is not None:
                self._observer.unschedule(watch)

    def watched_repos(self) -> set[tuple[str, str]]:
        return set(self._watches.keys())

    def resume_all(self) -> None:
        for repo in self._graph_store.list_repos():
            if os.path.isdir(repo["local_path"]):
                self.watch(repo["user_id"], repo["repo_id"], repo["local_path"])

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_repo_watcher.py -v`
Expected: all PASS, including every pre-existing test in the file (full `reindex()`/`watch()`/`resume_all()` behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/graph/repo_watcher.py tests/test_repo_watcher.py
git commit -m "$(cat <<'EOF'
feat(graph): incremental reindex -- only re-parse/re-embed changed files

_RepoChangeHandler now batches changed/deleted file paths across a
debounce window (_PendingChanges) instead of firing a blind
zero-argument callback. watch() wires that into the new
reindex_paths(), which re-parses only the changed files, merges them
against the existing graph (GraphStore.get_subgraph) to resolve
cross-file CALLS/IMPORTS/INHERITS, and writes/embeds only what
changed via replace_files_in_repo + selective Qdrant upsert/delete
keyed by symbol_point_id. Untouched files' symbols keep their id,
content_hash, and vector -- no re-parse, no re-embed.

reindex() (full parse) is unchanged, still used by ingest_codebase's
first-time ingest and as a manual full-rebuild fallback -- and now
also stores content_hash and uses symbol_point_id so the first
incremental reindex after it has something correct to diff against.

Renamed/deleted symbols leave any CALLS/IMPORTS/INHERITS edge from an
*unchanged* file pointing at the old id stale until that file is
itself edited -- accepted tradeoff, see the design spec's "Edge
staleness" section; test_reindex_paths_leaves_a_stale_edge_from_an_unchanged_file_after_a_rename
documents it as intentional.
EOF
)"
```

---

## Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ scripts/ -q --deselect tests/test_config.py::test_settings_usage_db_component_defaults --deselect tests/test_config.py::test_settings_deploy_mode_defaults`

Expected: all tests PASS (the two deselected tests are a pre-existing, unrelated `.env`-leak issue in this dev environment — confirmed failing identically on a clean `git stash` before this work started, not something this plan touches).

- [ ] **Step 2: If Neo4j is reachable, also run the integration suite**

Run: `NEO4J_TEST_URL=bolt://localhost:7687 NEO4J_TEST_PASSWORD=<your local password> .venv/bin/python -m pytest tests/test_code_graph_store_integration.py -v`

Expected: all PASS. If Neo4j isn't available in this environment, the whole file SKIPs — that's an acceptable outcome for this step, not a failure to chase.

- [ ] **Step 3: Manual smoke check (optional but recommended before merging)**

With `DEPLOY_MODE=local`, run the server, ingest a small local repo via the `ingest_codebase` MCP tool, edit one file in it, and watch server logs / query `export_graph_snapshot` before and after — confirm only the edited file's symbols show a new `content_hash`/id and the rest of the repo's node ids are unchanged. This isn't automatable in this plan (needs a running server + MCP client) but is worth doing once before considering the feature done, since the automated tests exercise `RepoWatcherManager` directly rather than through the full MCP-tool-to-watcher path.

- [ ] **Step 4: No commit for this task** — it's a verification checkpoint, not a code change. If Step 1 or 2 reveals a failure, fix it as part of whichever earlier task's commit is responsible (amend forward with a new commit, don't silently patch history), then re-run this task.

---

## Self-Review

**Spec coverage:**
- §1 (symbol identity & content hash) → Task 1.
- §2 (watcher batching) → Task 7, `_PendingChanges`/`_RepoChangeHandler`.
- §3 (orchestration: parse only changed, baseline merge, scoped resolve, embed diff, scoped write) → Task 7, `reindex_paths`; scoped write → Tasks 3–6 (`replace_files_in_repo` across all backends).
- §4 (deterministic Qdrant point id, selective upsert/delete) → Task 2 (`symbol_point_id`) + Task 7 (`_update_symbol_embeddings`).
- §5 (testing plan) → covered per-task; the "key proof" test (embed-call-count on a partial change) is `test_reindex_paths_only_reembeds_the_changed_file` in Task 7, plus the explicit stale-edge-is-intentional test.
- Rollout (no migration script, schema migration, no feature flag) → Task 3 Step 2 (guarded `ALTER TABLE`); no separate task needed for "no migration script"/"no feature flag" since those are absence-of-work, not tasks.

**Placeholder scan:** no TBD/TODO; every step has literal, complete code or an exact command with expected output.

**Type consistency:** `ParsedSymbol` gains `content_hash` in Task 1 and every downstream dict-builder (Task 7's `reindex`/`reindex_paths`) includes `"content_hash": s.content_hash` consistently. `GraphStore.replace_files_in_repo(repo, stale_file_paths, symbols, edges)` signature is identical across the abstract method (Task 3) and all four implementations (Tasks 4/5/6). `resolve_edges`'s `index_symbols: list[dict]` (keyed `id`/`name`/`kind`/`file_path`) is produced consistently by `_as_index_dicts` (Task 1, for the full-parse path) and inline in `reindex_paths` (Task 7, for the incremental path) — same four keys both places.
