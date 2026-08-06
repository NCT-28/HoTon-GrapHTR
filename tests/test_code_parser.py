from app.graph.code_parser import parse_repo


def test_parse_repo_extracts_python_symbols_defines_calls_imports_inherits(tmp_path):
    (tmp_path / "mod_a.py").write_text(
        "class Animal:\n"
        "    def speak(self):\n"
        "        pass\n"
        "\n"
        "class Dog(Animal):\n"
        "    def bark(self):\n"
        "        helper()\n"
        "\n"
        "def helper():\n"
        "    pass\n"
    )
    (tmp_path / "mod_b.py").write_text(
        "from mod_a import Dog\n"
        "\n"
        "def use_dog():\n"
        "    d = Dog()\n"
    )

    symbols, edges = parse_repo("r1", str(tmp_path))

    names = {s.name for s in symbols}
    assert {"Animal", "Dog", "helper", "speak", "bark", "use_dog"} <= names

    edge_types = {e.type for e in edges}
    assert edge_types == {"DEFINES", "CALLS", "IMPORTS", "INHERITS"}

    by_name = {s.name: s.id for s in symbols}
    inherits = [e for e in edges if e.type == "INHERITS"]
    assert any(e.source == by_name["Dog"] and e.target == by_name["Animal"] for e in inherits)

    calls = [e for e in edges if e.type == "CALLS"]
    assert any(e.source == by_name["bark"] and e.target == by_name["helper"] for e in calls)

    imports = [e for e in edges if e.type == "IMPORTS"]
    mod_a_id = next(s.id for s in symbols if s.kind == "module" and s.file_path.endswith("mod_a.py"))
    mod_b_id = next(s.id for s in symbols if s.kind == "module" and s.file_path.endswith("mod_b.py"))
    assert any(e.source == mod_b_id and e.target == mod_a_id for e in imports)


def test_parse_repo_handles_typescript(tmp_path):
    (tmp_path / "util.ts").write_text("export function add(a: number, b: number): number {\n  return a + b;\n}\n")

    symbols, _ = parse_repo("r1", str(tmp_path))

    assert any(s.name == "add" and s.kind == "function" for s in symbols)


def test_parse_repo_handles_rust(tmp_path):
    (tmp_path / "lib.rs").write_text("fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n")

    symbols, _ = parse_repo("r1", str(tmp_path))

    assert any(s.name == "add" and s.kind == "function" for s in symbols)


def test_parse_repo_ignores_unrecognized_files(tmp_path):
    (tmp_path / "README.md").write_text("# hello\n")

    symbols, edges = parse_repo("r1", str(tmp_path))

    assert symbols == []
    assert edges == []


def test_parse_repo_skips_ignored_directories(tmp_path):
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / "lib.js").write_text("function shouldNotAppear() {}\n")
    (tmp_path / "app.js").write_text("function shouldAppear() {}\n")

    symbols, _ = parse_repo("r1", str(tmp_path))

    names = {s.name for s in symbols}
    assert "shouldAppear" in names
    assert "shouldNotAppear" not in names


def test_parse_repo_symbol_ids_are_deterministic_across_repeated_parses(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")

    symbols_1, _ = parse_repo("r1", str(tmp_path))
    symbols_2, _ = parse_repo("r1", str(tmp_path))

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
    before = {s.name: (s.id, s.content_hash) for s in parse_repo("r1", str(tmp_path))[0]}

    path.write_text("\ndef foo():\n    pass\n\n\ndef bar():\n    pass\n")  # blank line inserted above foo
    after = {s.name: (s.id, s.content_hash) for s in parse_repo("r1", str(tmp_path))[0]}

    assert before["foo"] == after["foo"]  # id AND content_hash both stable
    assert before["bar"] == after["bar"]  # id AND content_hash both stable


def test_parse_repo_content_hash_changes_when_symbol_body_edited(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("def foo():\n    return 1\n")
    before = next(s for s in parse_repo("r1", str(tmp_path))[0] if s.name == "foo")

    path.write_text("def foo():\n    return 2\n")
    after = next(s for s in parse_repo("r1", str(tmp_path))[0] if s.name == "foo")

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

    symbols, _ = parse_repo("r1", str(tmp_path))

    init_ids = {s.id for s in symbols if s.name == "__init__"}
    assert len(init_ids) == 2


def test_parse_files_parses_only_the_given_paths(tmp_path):
    from app.graph.code_parser import parse_files

    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.py").write_text("def bar():\n    pass\n")

    symbols, defines, calls, imports, inherits = parse_files("r1", [str(tmp_path / "a.py")])

    names = {s.name for s in symbols}
    assert "foo" in names
    assert "bar" not in names


def test_resolve_edges_resolves_call_against_a_mixed_baseline_and_fresh_index():
    from app.graph.code_parser import resolve_edges

    # `bar` looks like it came from an already-indexed, unchanged file (a plain dict);
    # `foo` is freshly parsed this pass.
    baseline = [{"id": "bar-id", "name": "bar", "kind": "function", "file_path": "b.py"}]
    fresh = [{"id": "foo-id", "name": "foo", "kind": "function", "file_path": "a.py"}]

    edges = resolve_edges(baseline + fresh, [], [("foo-id", "bar")], [], [])

    assert len(edges) == 1
    assert edges[0].source == "foo-id"
    assert edges[0].target == "bar-id"
    assert edges[0].type == "CALLS"
