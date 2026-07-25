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


def _symbol_id(repo_id: str, file_path: str, kind: str, qualified_name: str) -> str:
    # repo_id is part of the hash input, not just file_path, so two different repos
    # that happen to share a file_path (e.g. the same local_path ingested twice under
    # different repo_ids) never collide on symbol id / Qdrant point id / sqlite PK.
    return hashlib.sha1(f"{repo_id}\x00{file_path}\x00{kind}\x00{qualified_name}".encode("utf8")).hexdigest()


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


def _parse_file(repo_id: str, file_path: str, ext: str):
    """Returns (symbols, resolved_defines, pending_calls, pending_imports, pending_inherits)
    where resolved_defines is [(parent_id, child_id)] (already known within
    this file) and the pending_* lists are [(source_id, target_name)] to be
    resolved against a repo-wide name index by resolve_edges()."""
    config = LANGUAGE_CONFIGS[ext]
    parser = get_parser(config.parser_name)

    with open(file_path, "rb") as f:
        source = f.read()
    tree = parser.parse(source)

    module_id = _symbol_id(repo_id, file_path, "module", "")
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
                symbol_id = _symbol_id(repo_id, file_path, kind, qualified_name)
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


def parse_files(repo_id: str, file_paths: list[str]):
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
        symbols, defines, calls, imports, inherits = _parse_file(repo_id, file_path, ext)
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


def parse_repo(repo_id: str, root_path: str) -> tuple[list[ParsedSymbol], list[ParsedEdge]]:
    """Walk `root_path`, parse every recognized file, and resolve
    CALLS/IMPORTS/INHERITS edges against a repo-wide name index built after
    all files are parsed."""
    file_paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        for filename in filenames:
            file_paths.append(os.path.join(dirpath, filename))

    all_symbols, resolved_defines, pending_calls, pending_imports, pending_inherits = parse_files(repo_id, file_paths)
    edges = resolve_edges(
        _as_index_dicts(all_symbols), resolved_defines, pending_calls, pending_imports, pending_inherits
    )
    return all_symbols, edges
