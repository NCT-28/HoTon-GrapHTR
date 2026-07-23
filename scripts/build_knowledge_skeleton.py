#!/usr/bin/env python3
"""Generates graphtr-out/knowledge/*.md skeletons: fact tables auto-filled from
graph.json/CLAUDE.md/repo manifests, plus <!-- TODO --> blocks for Claude to
fill in with narrative. Run via: python3 hoton-rag/scripts/build_knowledge_skeleton.py
"""
import json
import re
from collections import defaultdict
from pathlib import Path

def _find_repo_root(start: Path) -> Path:
    """Walk up from `start` looking for a `.git` directory. Marker-based (not a
    fixed parent-count) because this script is bundled at different depths in
    different projects: hoton-rag/scripts/ in this repo, but
    .claude/skills/graphtr-knowledge/scripts/ in a project this was installed
    into via init_graphtr_skills.py."""
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"could not find repo root (no .git found) walking up from {start}")


REPO_ROOT = _find_repo_root(Path(__file__).parent)
GRAPH_PATH = REPO_ROOT / "graphtr-out" / "graph.json"
OUT_DIR = REPO_ROOT / "graphtr-out" / "knowledge"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

SERVICES = ["hoton-lmr", "hoton-lmt", "hoton-lmu", "hoton-browser", "hoton-rag"]


def extract_section(text: str, heading: str) -> str:
    """Return the body of a markdown section (heading exclusive), stopping at
    the next heading of the same or shallower level. Subsections (deeper
    headings) are included."""
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading.strip():
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for i in range(start, len(lines)):
        stripped = lines[i]
        if stripped.startswith("#"):
            cur_level = len(stripped) - len(stripped.lstrip("#"))
            if cur_level <= level:
                end = i
                break
    return "\n".join(lines[start:end]).strip()


def _cargo_deps(path: Path) -> str:
    text = path.read_text()
    m = re.search(r"^\[dependencies\](.*?)(?=^\[|\Z)", text, re.S | re.M)
    if not m:
        return ""
    names = re.findall(r"^([A-Za-z0-9_-]+)\s*=", m.group(1), re.M)
    return ", ".join(f"`{n}`" for n in names[:8])


def _package_json_deps(path: Path) -> str:
    data = json.loads(path.read_text())
    names = list(data.get("dependencies", {}).keys())
    return ", ".join(f"`{n}`" for n in names[:8])


def _requirements_deps(path: Path) -> str:
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]
    names = [re.split(r"[=<>~\[]", l)[0] for l in lines]
    return ", ".join(f"`{n}`" for n in names[:8])


def detect_stack() -> str:
    rows = ["| Service | Language | Manifest | Key deps |", "|---|---|---|---|"]
    for svc in SERVICES:
        svc_dir = REPO_ROOT / svc
        if not svc_dir.is_dir():
            continue
        cargo = svc_dir / "Cargo.toml"
        pkg = svc_dir / "package.json"
        req = svc_dir / "requirements.txt"
        if cargo.exists():
            lang, manifest, deps = "Rust", "Cargo.toml", _cargo_deps(cargo)
        elif pkg.exists():
            lang, manifest, deps = "TypeScript/JS", "package.json", _package_json_deps(pkg)
        elif req.exists():
            lang, manifest, deps = "Python", "requirements.txt", _requirements_deps(req)
        else:
            continue
        rows.append(f"| `{svc}` | {lang} | `{manifest}` | {deps} |")
    return "\n".join(rows)


def detect_structure() -> str:
    data = json.loads(GRAPH_PATH.read_text())
    counts = defaultdict(int)
    for n in data["nodes"]:
        fp = n.get("file_path")
        if not fp:
            continue
        top = fp.split("/")[0]
        counts[top] += 1
    rows = ["| Directory | Symbol count |", "|---|---|"]
    for top, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        rows.append(f"| `{top}` | {count} |")
    return "\n".join(rows)


def detect_testing() -> str:
    lines = []
    if (REPO_ROOT / "hoton-lmr" / "Cargo.toml").exists():
        lines.append("- `hoton-lmr`: `cargo test` (single test: `cargo test <test_name>`)")
    pkg_path = REPO_ROOT / "hoton-lmu" / "package.json"
    if pkg_path.exists():
        scripts = json.loads(pkg_path.read_text()).get("scripts", {})
        for name in ("lint", "typecheck", "test"):
            if name in scripts:
                lines.append(f"- `hoton-lmu`: `npm run {name}` -> `{scripts[name]}`")
    for svc in ("hoton-lmt", "hoton-rag"):
        tests_dir = REPO_ROOT / svc / "tests"
        if tests_dir.is_dir():
            count = len(list(tests_dir.glob("test_*.py")))
            lines.append(f"- `{svc}`: pytest, {count} test file(s) under `{svc}/tests/`")
    return "\n".join(lines) if lines else "_no test tooling detected_"


def detect_conventions() -> str:
    lines = []
    if (REPO_ROOT / "hoton-lmu" / "biome.json").exists():
        lines.append("- `hoton-lmu`: Biome (`biome.json`) -- run `npm run biome:check` / `npm run biome:fix`")
    if (REPO_ROOT / "hoton-lmr" / "rustfmt.toml").exists():
        lines.append("- `hoton-lmr`: `rustfmt.toml` present")
    return "\n".join(lines) if lines else "_no lint/format config detected_"


TITLES = {
    "architecture": "Architecture",
    "concerns": "Concerns",
    "conventions": "Conventions",
    "integrations": "Integrations",
    "stack": "Stack",
    "structure": "Structure",
    "testing": "Testing",
}


def write_skeleton(topic: str, title: str, facts_md: str) -> None:
    facts_block = facts_md if facts_md.strip() else "_no auto-detected facts for this topic -- narrative only._"
    content = (
        f"# {title}\n\n"
        "<!-- AUTO-GENERATED by build_knowledge_skeleton.py -- facts below are derived "
        "from repo/graph.json, safe to regenerate -->\n\n"
        "## Facts\n\n"
        f"{facts_block}\n\n"
        "<!-- TODO: narrative -- replace this whole block. Explain the WHY behind the "
        "facts above. -->\n\n"
        "## Narrative\n\n"
        "<!-- TODO: fill in -->\n"
    )
    (OUT_DIR / f"{topic}.md").write_text(content)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    claude_md = CLAUDE_MD.read_text()
    facts = {
        "architecture": extract_section(claude_md, "## Service Map"),
        "concerns": "",
        "conventions": detect_conventions(),
        "integrations": extract_section(claude_md, "### External integrations"),
        "stack": detect_stack(),
        "structure": detect_structure(),
        "testing": detect_testing(),
    }
    for topic, title in TITLES.items():
        write_skeleton(topic, title, facts[topic])
    print(f"Wrote {len(TITLES)} skeleton files to {OUT_DIR}")


if __name__ == "__main__":
    main()
