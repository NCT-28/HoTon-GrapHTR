from app.code_parser import parse_repo


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

    symbols, edges = parse_repo(str(tmp_path))

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

    symbols, _ = parse_repo(str(tmp_path))

    assert any(s.name == "add" and s.kind == "function" for s in symbols)


def test_parse_repo_handles_rust(tmp_path):
    (tmp_path / "lib.rs").write_text("fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n")

    symbols, _ = parse_repo(str(tmp_path))

    assert any(s.name == "add" and s.kind == "function" for s in symbols)


def test_parse_repo_ignores_unrecognized_files(tmp_path):
    (tmp_path / "README.md").write_text("# hello\n")

    symbols, edges = parse_repo(str(tmp_path))

    assert symbols == []
    assert edges == []


def test_parse_repo_skips_ignored_directories(tmp_path):
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / "lib.js").write_text("function shouldNotAppear() {}\n")
    (tmp_path / "app.js").write_text("function shouldAppear() {}\n")

    symbols, _ = parse_repo(str(tmp_path))

    names = {s.name for s in symbols}
    assert "shouldAppear" in names
    assert "shouldNotAppear" not in names
