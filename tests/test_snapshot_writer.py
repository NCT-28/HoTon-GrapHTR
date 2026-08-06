import json
import os

from app.graph.code_parser import ParsedEdge, ParsedSymbol
from app.graph.snapshot_writer import render_viewer, write_graph_snapshot

SYMBOLS = [
    ParsedSymbol(id="s1", kind="function", name="foo", file_path="a.py",
                 start_line=1, end_line=3, language="python", content_hash="h1"),
    ParsedSymbol(id="s2", kind="class", name="Bar", file_path="b.py",
                 start_line=1, end_line=9, language="python", content_hash="h2"),
]
EDGES = [ParsedEdge(source="s1", target="s2", type="CALLS")]


def test_write_graph_snapshot_writes_nodes_and_edges(tmp_path):
    out_dir = write_graph_snapshot(str(tmp_path), "r1", SYMBOLS, EDGES)

    graph = json.loads((tmp_path / "graphtr-out" / "graph.json").read_text())
    assert out_dir == str(tmp_path / "graphtr-out")
    assert [n["id"] for n in graph["nodes"]] == ["s1", "s2"]
    assert graph["nodes"][0] == {
        "id": "s1", "name": "foo", "kind": "function", "file_path": "a.py",
        "start_line": 1, "end_line": 3, "language": "python",
    }
    assert graph["edges"] == [{"source": "s1", "target": "s2", "type": "CALLS"}]


def test_write_graph_snapshot_tallies_kinds_and_types_into_manifest(tmp_path):
    write_graph_snapshot(str(tmp_path), "r1", SYMBOLS, EDGES)

    manifest = json.loads((tmp_path / "graphtr-out" / "manifest.json").read_text())
    assert manifest["repo_id"] == "r1"
    assert manifest["node_count"] == 2
    assert manifest["edge_count"] == 1
    assert manifest["node_kinds"] == {"function": 1, "class": 1}
    assert manifest["edge_types"] == {"CALLS": 1}
    assert manifest["last_indexed_at"]
    assert manifest["exported_at"]
    # code symbols are no longer embedded, so this stat is gone entirely
    assert "code_symbol_count" not in manifest


def test_write_graph_snapshot_preserves_foreign_manifest_keys(tmp_path):
    # scripts/index_knowledge.py mints rag_user_id into this same file; a blind
    # overwrite would orphan every knowledge doc indexed under it.
    out_dir = tmp_path / "graphtr-out"
    os.makedirs(out_dir)
    (out_dir / "manifest.json").write_text(json.dumps({"rag_user_id": "keep-me", "node_count": 999}))

    write_graph_snapshot(str(tmp_path), "r1", SYMBOLS, EDGES)

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["rag_user_id"] == "keep-me"
    assert manifest["node_count"] == 2


def test_render_viewer_writes_html_from_graph_json(tmp_path):
    out_dir = write_graph_snapshot(str(tmp_path), "r1", SYMBOLS, EDGES)

    render_viewer(out_dir)

    html = (tmp_path / "graphtr-out" / "graphtr.html").read_text()
    assert "foo" in html
    assert "r1" in html
