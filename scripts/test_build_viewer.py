#!/usr/bin/env python3
"""Sanity tests for build_viewer.py's pure data-shaping + templating."""
import json

from build_viewer import build_data, main


NODES = [
    {"id": "a", "name": "foo", "kind": "function", "file_path": "a.py", "start_line": 1, "end_line": 2},
    {"id": "b", "name": "Bar", "kind": "class", "file_path": "b.py", "start_line": 1, "end_line": 5},
]
EDGES = [{"source": "a", "target": "b", "type": "CALLS"}]


def test_build_data_returns_edge_legend_with_counts_and_colors():
    _, _, _, edge_legend = build_data(NODES, EDGES)

    assert edge_legend == [{"type": "CALLS", "color": "#59A14F", "label": "CALLS", "count": 1}]


def test_build_data_edge_legend_sorted_by_count_descending():
    edges = [
        {"source": "a", "target": "b", "type": "CALLS"},
        {"source": "a", "target": "b", "type": "IMPORTS"},
        {"source": "a", "target": "b", "type": "IMPORTS"},
    ]

    _, _, _, edge_legend = build_data(NODES, edges)

    assert [e["type"] for e in edge_legend] == ["IMPORTS", "CALLS"]


def test_main_bakes_manifest_last_indexed_at_into_html(tmp_path):
    out_dir = tmp_path
    (out_dir / "graph.json").write_text(json.dumps({"nodes": NODES, "edges": EDGES}))
    (out_dir / "manifest.json").write_text(json.dumps({
        "repo_id": "r1", "last_indexed_at": "2026-07-25T00:00:00",
    }))

    import sys
    old_argv = sys.argv
    sys.argv = ["build_viewer.py", "--out-dir", str(out_dir)]
    try:
        main()
    finally:
        sys.argv = old_argv

    html = (out_dir / "graphtr.html").read_text()
    assert '"last_indexed_at":"2026-07-25T00:00:00"' in html
    assert "EDGE_LEGEND" in html


def test_build_is_callable_without_argv(tmp_path):
    from build_viewer import build

    (tmp_path / "graph.json").write_text(json.dumps({"nodes": NODES, "edges": EDGES}))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "repo_id": "r1", "last_indexed_at": "2026-08-06T00:00:00",
    }))

    build(tmp_path)

    html = (tmp_path / "graphtr.html").read_text()
    assert "r1" in html
    assert "2026-08-06T00:00:00" in html
