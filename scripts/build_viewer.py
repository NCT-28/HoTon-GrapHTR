#!/usr/bin/env python3
"""Build graphtr-out/graphtr.html — an interactive viewer for graph.json.

Same approach/style as graphify-out/graph.html: vis-network (CDN) with
forceAtlas2Based physics for layout, a sidebar (search / info panel / legend),
color+size encode kind/degree. Node/edge positions are computed live by
vis-network's physics engine in the browser — this script only shapes the data.

Usage:
  python3 graphtr-out/build_viewer.py

Pass --out-dir <dir> to build a graphtr.html for a graph.json living
somewhere other than this script's own directory (e.g. a shared copy of this
script run against a different project's graphtr-out/):
  python3 build_viewer.py --out-dir /path/to/other/graphtr-out
"""
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_OUT_DIR = Path(__file__).parent

# Tableau10, same palette family as graphify-out/graph.html
KIND_COLORS = {
    "module": "#4E79A7",
    "class": "#F28E2B",
    "function": "#59A14F",
    "method": "#E15759",
    "text_entity": "#B07AA1",
    "unknown": "#BAB0AC",
}
EDGE_COLORS = {
    "CALLS": "#59A14F",
    "DEFINES": "#BAB0AC",
    "IMPORTS": "#4E79A7",
    "INHERITS": "#E15759",
    "MENTIONS": "#B07AA1",
}
KIND_BASE_SIZE = {
    "module": 16,
    "class": 11,
    "function": 7,
    "method": 7,
    "text_entity": 9,
    "unknown": 7,
}


def node_label(n: dict) -> str:
    kind = n.get("kind") or "unknown"
    if kind == "module" and n.get("file_path"):
        return n["file_path"]
    return n["name"]


def build_data(nodes: list[dict], edges: list[dict]):
    degree = Counter()
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1

    raw_nodes = []
    for n in nodes:
        kind = n.get("kind") or "unknown"
        color = KIND_COLORS.get(kind, KIND_COLORS["unknown"])
        d = degree[n["id"]]
        size = KIND_BASE_SIZE.get(kind, 7) + min(d, 60) * 0.5
        loc = n.get("file_path") or ""
        if n.get("start_line"):
            loc += f":{n['start_line']}-{n.get('end_line', n['start_line'])}"
        raw_nodes.append({
            "id": n["id"],
            "label": node_label(n),
            "color": {"background": color, "border": color, "highlight": {"background": color, "border": "#ffffff"}},
            "size": round(size, 1),
            "font": {"color": "#c0caf5", "size": 11 if kind != "module" else 13},
            "title": f"{node_label(n)}\n{kind}" + (f"\n{loc}" if loc else ""),
            "_kind": kind,
            "_file_path": n.get("file_path"),
            "_start_line": n.get("start_line"),
            "_end_line": n.get("end_line"),
            "_degree": d,
        })

    raw_edges = []
    for e in edges:
        color = EDGE_COLORS.get(e["type"], "#565f89")
        raw_edges.append({
            "from": e["source"],
            "to": e["target"],
            "title": e["type"],
            "color": {"color": color, "opacity": 0.35},
            "width": 1,
        })

    kind_counts = Counter(n.get("kind") or "unknown" for n in nodes)
    legend = [
        {"kind": k, "color": KIND_COLORS.get(k, KIND_COLORS["unknown"]), "label": k, "count": kind_counts[k]}
        for k in sorted(kind_counts, key=lambda k: -kind_counts[k])
    ]

    return raw_nodes, raw_edges, legend


def main():
    args = sys.argv[1:]
    out_dir = DEFAULT_OUT_DIR
    if len(args) >= 2 and args[0] == "--out-dir":
        out_dir = Path(args[1])

    graph_path = out_dir / "graph.json"
    manifest_path = out_dir / "manifest.json"
    output_path = out_dir / "graphtr.html"

    graph = json.loads(graph_path.read_text())
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    raw_nodes, raw_edges, legend = build_data(graph["nodes"], graph["edges"])

    def js_json(obj) -> str:
        # Escape "</" so embedded strings can't prematurely close the <script> tag.
        return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")

    html = (
        TEMPLATE
        .replace("__RAW_NODES__", js_json(raw_nodes))
        .replace("__RAW_EDGES__", js_json(raw_edges))
        .replace("__LEGEND__", js_json(legend))
        .replace("__MANIFEST__", js_json(manifest))
    )
    output_path.write_text(html)
    print(f"wrote {output_path} ({len(raw_nodes)} nodes, {len(raw_edges)} edges)")


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>graphtr - graphtr-out/graphtr.html</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #1a1b26; color: #c0caf5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; height: 100vh; overflow: hidden; }
  #graph { flex: 1; }
  #sidebar { width: 300px; background: #16161e; border-left: 1px solid #2a2e42; display: flex; flex-direction: column; overflow: hidden; }
  #search-wrap { padding: 12px; border-bottom: 1px solid #2a2e42; }
  #search { width: 100%; background: #1a1b26; border: 1px solid #2a2e42; color: #c0caf5; padding: 7px 10px; border-radius: 6px; font-size: 13px; outline: none; }
  #search:focus { border-color: #7aa2f7; }
  #search-results { max-height: 160px; overflow-y: auto; padding: 4px 12px; border-bottom: 1px solid #2a2e42; display: none; }
  .search-item { padding: 4px 6px; cursor: pointer; border-radius: 4px; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .search-item:hover { background: #2a2e42; }
  #info-panel { padding: 14px; border-bottom: 1px solid #2a2e42; min-height: 140px; }
  #info-panel h3 { font-size: 13px; color: #7982a9; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }
  #info-content { font-size: 13px; color: #c0caf5; line-height: 1.6; }
  #info-content .field { margin-bottom: 5px; word-break: break-word; }
  #info-content .field b { color: #e0e0e0; }
  #info-content .empty { color: #565f89; font-style: italic; }
  .neighbor-link { display: block; padding: 2px 6px; margin: 2px 0; border-radius: 3px; cursor: pointer; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-left: 3px solid #333; }
  .neighbor-link:hover { background: #2a2e42; }
  #neighbors-list { max-height: 180px; overflow-y: auto; margin-top: 4px; }
  #legend-wrap { flex: 1; overflow-y: auto; padding: 12px; }
  #legend-wrap h3 { font-size: 13px; color: #7982a9; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
  .legend-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; cursor: pointer; border-radius: 4px; font-size: 12px; }
  .legend-item:hover { background: #2a2e42; padding-left: 4px; }
  .legend-item.dimmed { opacity: 0.35; }
  .legend-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
  .legend-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .legend-count { color: #565f89; font-size: 11px; }
  #stats { padding: 10px 14px; border-top: 1px solid #2a2e42; font-size: 11px; color: #565f89; }
  #legend-controls { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; padding: 4px 0; }
  #legend-controls label { display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 12px; color: #7982a9; user-select: none; }
  #legend-controls label:hover { color: #c0caf5; }
</style>
</head>
<body>
<div id="graph"></div>
<div id="sidebar">
  <div id="search-wrap">
    <input id="search" type="text" placeholder="search symbol name...">
    <div id="search-results"></div>
  </div>
  <div id="info-panel">
    <h3>Details</h3>
    <div id="info-content"><span class="empty">Click a node to inspect it</span></div>
  </div>
  <div id="legend-wrap">
    <h3>Kinds</h3>
    <div id="legend-controls">
      <label><input type="checkbox" id="select-all-cb" checked> <span>Show all</span></label>
    </div>
    <div id="legend"></div>
  </div>
  <div id="stats"></div>
</div>

<script>
const RAW_NODES = __RAW_NODES__;
const RAW_EDGES = __RAW_EDGES__;
const LEGEND = __LEGEND__;
const MANIFEST = __MANIFEST__;

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

const nodesDS = new vis.DataSet(RAW_NODES.map(n => ({
  id: n.id, label: n.label, color: n.color, size: n.size, font: n.font, title: n.title,
  _kind: n._kind, _file_path: n._file_path, _start_line: n._start_line, _end_line: n._end_line, _degree: n._degree,
})));

const edgesDS = new vis.DataSet(RAW_EDGES.map((e, i) => ({
  id: i, from: e.from, to: e.to, title: e.title, color: e.color, width: e.width,
  arrows: { to: { enabled: true, scaleFactor: 0.4 } },
})));

const container = document.getElementById('graph');
const network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, {
  physics: {
    enabled: true,
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {
      gravitationalConstant: -60,
      centralGravity: 0.005,
      springLength: 120,
      springConstant: 0.08,
      damping: 0.4,
      avoidOverlap: 0.8,
    },
    stabilization: { iterations: 200, fit: true },
  },
  interaction: { hover: true, tooltipDelay: 100, hideEdgesOnDrag: true, navigationButtons: false, keyboard: false },
  nodes: { shape: 'dot', borderWidth: 1.5 },
  edges: { smooth: { type: 'continuous', roundness: 0.2 }, selectionWidth: 3 },
});

network.once('stabilizationIterationsDone', () => {
  network.setOptions({ physics: { enabled: false } });
});

function showInfo(nodeId) {
  const n = nodesDS.get(nodeId);
  if (!n) return;
  const neighborIds = network.getConnectedNodes(nodeId);
  const neighborItems = neighborIds.map(nid => {
    const nb = nodesDS.get(nid);
    const color = nb ? nb.color.background : '#555';
    return `<span class="neighbor-link" style="border-left-color:${esc(color)}" onclick="focusNode(${JSON.stringify(nid)})">${esc(nb ? nb.label : nid)}</span>`;
  }).join('');
  const loc = n._file_path ? `${n._file_path}${n._start_line ? ':' + n._start_line + '-' + n._end_line : ''}` : '-';
  document.getElementById('info-content').innerHTML = `
    <div class="field"><b>${esc(n.label)}</b></div>
    <div class="field">Kind: ${esc(n._kind)}</div>
    <div class="field">File: ${esc(loc)}</div>
    <div class="field">Degree: ${n._degree}</div>
    ${neighborIds.length ? `<div class="field" style="margin-top:8px;color:#7982a9;font-size:11px">Neighbors (${neighborIds.length})</div><div id="neighbors-list">${neighborItems}</div>` : ''}
  `;
}

function focusNode(nodeId) {
  network.focus(nodeId, { scale: 1.4, animation: true });
  network.selectNodes([nodeId]);
  showInfo(nodeId);
}

let hoveredNodeId = null;
network.on('hoverNode', params => { hoveredNodeId = params.node; container.style.cursor = 'pointer'; });
network.on('blurNode', () => { hoveredNodeId = null; container.style.cursor = 'default'; });
network.on('click', params => {
  if (params.nodes.length > 0) {
    showInfo(params.nodes[0]);
  } else if (hoveredNodeId === null) {
    document.getElementById('info-content').innerHTML = '<span class="empty">Click a node to inspect it</span>';
  }
});

const searchInput = document.getElementById('search');
const searchResults = document.getElementById('search-results');
searchInput.addEventListener('input', () => {
  const q = searchInput.value.toLowerCase().trim();
  searchResults.innerHTML = '';
  if (!q) { searchResults.style.display = 'none'; return; }
  const matches = RAW_NODES.filter(n => n.label.toLowerCase().includes(q)).slice(0, 20);
  if (!matches.length) { searchResults.style.display = 'none'; return; }
  searchResults.style.display = 'block';
  matches.forEach(n => {
    const el = document.createElement('div');
    el.className = 'search-item';
    el.textContent = n.label;
    el.style.borderLeft = `3px solid ${n.color.background}`;
    el.style.paddingLeft = '8px';
    el.onclick = () => {
      network.focus(n.id, { scale: 1.5, animation: true });
      network.selectNodes([n.id]);
      showInfo(n.id);
      searchResults.style.display = 'none';
      searchInput.value = '';
    };
    searchResults.appendChild(el);
  });
});
document.addEventListener('click', e => {
  if (!searchResults.contains(e.target) && e.target !== searchInput) searchResults.style.display = 'none';
});

const hiddenKinds = new Set();
const selectAllCb = document.getElementById('select-all-cb');

function updateSelectAllState() {
  const total = LEGEND.length;
  const hidden = hiddenKinds.size;
  selectAllCb.checked = hidden === 0;
  selectAllCb.indeterminate = hidden > 0 && hidden < total;
}

selectAllCb.addEventListener('change', () => toggleAllKinds(!selectAllCb.checked));

function toggleAllKinds(hide) {
  document.querySelectorAll('.legend-item').forEach(item => {
    hide ? item.classList.add('dimmed') : item.classList.remove('dimmed');
  });
  document.querySelectorAll('.legend-cb').forEach(cb => { cb.checked = !hide; });
  LEGEND.forEach(l => { hide ? hiddenKinds.add(l.kind) : hiddenKinds.delete(l.kind); });
  const updates = RAW_NODES.filter(n => true).map(n => ({ id: n.id, hidden: hide }));
  nodesDS.update(updates);
  updateSelectAllState();
}

const legendEl = document.getElementById('legend');
LEGEND.forEach(l => {
  const item = document.createElement('div');
  item.className = 'legend-item';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.className = 'legend-cb';
  cb.checked = true;
  cb.addEventListener('change', (e) => {
    e.stopPropagation();
    if (cb.checked) { hiddenKinds.delete(l.kind); item.classList.remove('dimmed'); }
    else { hiddenKinds.add(l.kind); item.classList.add('dimmed'); }
    const updates = RAW_NODES.filter(n => n._kind === l.kind).map(n => ({ id: n.id, hidden: !cb.checked }));
    nodesDS.update(updates);
    updateSelectAllState();
  });
  item.innerHTML = `<div class="legend-dot" style="background:${l.color}"></div>
    <span class="legend-label">${esc(l.label)}</span>
    <span class="legend-count">${l.count}</span>`;
  item.prepend(cb);
  item.onclick = (e) => { if (e.target === cb) return; cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); };
  legendEl.appendChild(item);
});

document.getElementById('stats').innerHTML = `
  repo_id: ${esc(MANIFEST.repo_id || '?')}<br>
  nodes: ${RAW_NODES.length} &middot; edges: ${RAW_EDGES.length}<br>
  exported: ${esc(MANIFEST.exported_at || '?')}
`;
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
