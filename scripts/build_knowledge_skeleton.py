#!/usr/bin/env python3
"""Generates graphtr-out/knowledge/*.md skeletons: fact tables auto-filled from
graph.json/CLAUDE.md/repo manifests, plus <!-- TODO --> blocks for Claude to
fill in with narrative.

Run via: python3 scripts/build_knowledge_skeleton.py [topic ...]

With no args, regenerates all 7 topics. With topic names given (e.g.
`stack conventions`), only those are touched -- for the `update` case where
only some facts changed and the rest shouldn't be re-rolled.

Facts are always regenerated from source, but an already-written Narrative
section (anything other than the `<!-- TODO: fill in -->` placeholder) is
preserved across regeneration instead of being wiped back to a TODO -- so
re-running this for a fact-only change (`update`) doesn't destroy prose
already written for that topic.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

def _find_repo_root(start: Path) -> Path:
    """Walk up from `start` looking for a `.git` directory. Marker-based (not a
    fixed parent-count) because this script is bundled at different depths in
    different projects: scripts/ in this repo, but
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

def extract_first_section(text: str, headings: list[str]) -> str:
    """Try each heading in order, return the first section that's non-empty.
    CLAUDE.md's actual section names vary per repo (e.g. `## Service Map` in
    a monorepo vs plain `## Architecture` here) -- no single hardcoded heading
    works everywhere, so try likely candidates instead of just one."""
    for heading in headings:
        section = extract_section(text, heading)
        if section:
            return section
    return ""


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


MANIFEST_KINDS = [
    ("Cargo.toml", "Rust", _cargo_deps),
    ("package.json", "TypeScript/JS", _package_json_deps),
    ("requirements.txt", "Python", _requirements_deps),
]


def _discover_service_dirs() -> list[Path]:
    """This script is bundled into other projects via init_graphtr_skills.py,
    so it can't hardcode service names for one specific repo. A repo is
    either single-service (manifest at REPO_ROOT) or a monorepo of sibling
    service dirs (manifest one level down) -- detect which generically by
    manifest presence instead."""
    if any((REPO_ROOT / name).exists() for name, _, _ in MANIFEST_KINDS):
        return [REPO_ROOT]
    return sorted(
        d for d in REPO_ROOT.iterdir()
        if d.is_dir() and any((d / name).exists() for name, _, _ in MANIFEST_KINDS)
    )


def _service_label(svc_dir: Path) -> str:
    return svc_dir.name if svc_dir != REPO_ROOT else REPO_ROOT.name


def detect_stack() -> str:
    rows = ["| Service | Language | Manifest | Key deps |", "|---|---|---|---|"]
    for svc_dir in _discover_service_dirs():
        for manifest_name, lang, extractor in MANIFEST_KINDS:
            manifest = svc_dir / manifest_name
            if manifest.exists():
                rows.append(f"| `{_service_label(svc_dir)}` | {lang} | `{manifest_name}` | {extractor(manifest)} |")
                break
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
    for svc_dir in _discover_service_dirs():
        label = _service_label(svc_dir)
        if (svc_dir / "Cargo.toml").exists():
            lines.append(f"- `{label}`: `cargo test` (single test: `cargo test <test_name>`)")
        pkg_path = svc_dir / "package.json"
        if pkg_path.exists():
            scripts = json.loads(pkg_path.read_text()).get("scripts", {})
            for name in ("lint", "typecheck", "test"):
                if name in scripts:
                    lines.append(f"- `{label}`: `npm run {name}` -> `{scripts[name]}`")
        tests_dir = svc_dir / "tests"
        if tests_dir.is_dir():
            count = len(list(tests_dir.glob("test_*.py")))
            rel = "tests/" if svc_dir == REPO_ROOT else f"{label}/tests/"
            lines.append(f"- `{label}`: pytest, {count} test file(s) under `{rel}`")
    return "\n".join(lines) if lines else "_no test tooling detected_"


def detect_conventions() -> str:
    lines = []
    for svc_dir in _discover_service_dirs():
        label = _service_label(svc_dir)
        if (svc_dir / "biome.json").exists():
            lines.append(f"- `{label}`: Biome (`biome.json`) -- run `npm run biome:check` / `npm run biome:fix`")
        if (svc_dir / "rustfmt.toml").exists():
            lines.append(f"- `{label}`: `rustfmt.toml` present")
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


TODO_NARRATIVE_MARKER = "<!-- TODO: fill in -->"


def _existing_narrative(topic: str) -> str | None:
    """Return the previously-written Narrative section for `topic`, or None if
    the file doesn't exist yet or still has the unfilled TODO placeholder."""
    path = OUT_DIR / f"{topic}.md"
    if not path.exists():
        return None
    narrative = extract_section(path.read_text(), "## Narrative")
    if not narrative or TODO_NARRATIVE_MARKER in narrative:
        return None
    return narrative


def write_skeleton(topic: str, title: str, facts_md: str) -> None:
    facts_block = facts_md if facts_md.strip() else "_no auto-detected facts for this topic -- narrative only._"
    preserved = _existing_narrative(topic)
    narrative_block = preserved if preserved is not None else TODO_NARRATIVE_MARKER
    content = (
        f"# {title}\n\n"
        "<!-- AUTO-GENERATED by build_knowledge_skeleton.py -- facts below are derived "
        "from repo/graph.json, safe to regenerate -->\n\n"
        "## Facts\n\n"
        f"{facts_block}\n\n"
        "<!-- TODO: narrative -- replace this whole block. Explain the WHY behind the "
        "facts above. -->\n\n"
        "## Narrative\n\n"
        f"{narrative_block}\n"
    )
    (OUT_DIR / f"{topic}.md").write_text(content)


def main() -> None:
    requested = sys.argv[1:]
    if requested:
        unknown = [t for t in requested if t not in TITLES]
        if unknown:
            print(f"FAIL: unknown topic(s) {unknown} -- valid topics: {sorted(TITLES)}")
            sys.exit(1)
        topics = requested
    else:
        topics = list(TITLES)

    OUT_DIR.mkdir(exist_ok=True)
    claude_md = CLAUDE_MD.read_text()
    facts = {
        "architecture": extract_first_section(claude_md, ["## Service Map", "## Architecture"]),
        "concerns": "",
        "conventions": detect_conventions(),
        "integrations": extract_first_section(claude_md, ["### External integrations", "## Integrations"]),
        "stack": detect_stack(),
        "structure": detect_structure(),
        "testing": detect_testing(),
    }
    for topic in topics:
        write_skeleton(topic, TITLES[topic], facts[topic])
    print(f"Wrote {len(topics)} skeleton file(s) to {OUT_DIR}")


if __name__ == "__main__":
    main()
