"""Writes a parsed repo's code graph to <repo>/graphtr-out/ as the final
artifact: graph.json + manifest.json + graphtr.html. This is the whole
persistence story for the code graph -- nothing is kept server-side."""

import datetime
import importlib.util
import json
import os
from pathlib import Path

# scripts/ is deliberately not a package (its files are copied standalone into
# other projects by init_graphtr_skills.py, and scripts/test_*.py import their
# subject bare), so the renderer is loaded by path rather than imported.
_BUILD_VIEWER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_viewer.py"


def _load_build():
    spec = importlib.util.spec_from_file_location("graphtr_build_viewer", _BUILD_VIEWER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build


def write_graph_snapshot(local_path: str, repo_id: str, symbols: list, edges: list) -> str:
    out_dir = os.path.join(local_path, "graphtr-out")
    os.makedirs(out_dir, exist_ok=True)

    nodes = [
        {"id": s.id, "name": s.name, "kind": s.kind, "file_path": s.file_path,
         "start_line": s.start_line, "end_line": s.end_line, "language": s.language}
        for s in symbols
    ]
    edge_dicts = [{"source": e.source, "target": e.target, "type": e.type} for e in edges]

    node_kinds: dict[str, int] = {}
    for s in symbols:
        kind = s.kind or "unknown"
        node_kinds[kind] = node_kinds.get(kind, 0) + 1

    edge_types: dict[str, int] = {}
    for e in edges:
        edge_types[e.type] = edge_types.get(e.type, 0) + 1

    with open(os.path.join(out_dir, "graph.json"), "w") as f:
        json.dump({"nodes": nodes, "edges": edge_dicts}, f)

    now = datetime.datetime.utcnow().isoformat()
    manifest_path = os.path.join(out_dir, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError):
            manifest = {}
    manifest.update({
        "repo_id": repo_id,
        "node_count": len(nodes),
        "edge_count": len(edge_dicts),
        "node_kinds": node_kinds,
        "edge_types": edge_types,
        "last_indexed_at": now,
        "exported_at": now,
    })
    manifest.pop("code_symbol_count", None)  # stale: code symbols are no longer embedded
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)

    return out_dir


def render_viewer(out_dir: str) -> None:
    _load_build()(Path(out_dir))
