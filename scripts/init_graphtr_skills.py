#!/usr/bin/env python3
"""Installs the graphtr + graphtr-knowledge skills into a target project so it
can adopt the same hoton-graphtr-backed code-graph + knowledge-base workflow
this repo (HoTon-GrapHTR, hoton-graphtr's own source repo) provides. Copies:
  - .claude/skills/graphtr/            -> <target>/.claude/skills/graphtr/
  - .claude/skills/graphtr-knowledge/  -> <target>/.claude/skills/graphtr-knowledge/
    (SKILL.md path references rewritten: this repo's own build_knowledge_skeleton.py/
    index_knowledge.py live at scripts/ (repo root), but a target project has no
    hoton-graphtr source of its own -- its copy is bundled directly under the
    skill directory instead, at <target>/.claude/skills/graphtr-knowledge/scripts/.
    Both locations work unmodified because they locate their repo root by
    walking up to a .git marker, not a fixed parent-count.)
  - graphtr-out/query.py, build_viewer.py -> <target>/graphtr-out/ (generic,
    no project-specific data)

Run via: python3 scripts/init_graphtr_skills.py /path/to/target/project
"""
import shutil
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"could not find repo root (no .git found) walking up from {start}")


THIS_REPO_ROOT = _find_repo_root(Path(__file__).parent)

SOURCE_GRAPHTR_SKILL = THIS_REPO_ROOT / ".claude" / "skills" / "graphtr"
SOURCE_GRAPHTR_KNOWLEDGE_SKILL_MD = THIS_REPO_ROOT / ".claude" / "skills" / "graphtr-knowledge" / "SKILL.md"
SOURCE_KNOWLEDGE_SCRIPTS = [
    THIS_REPO_ROOT / "scripts" / "build_knowledge_skeleton.py",
    THIS_REPO_ROOT / "scripts" / "index_knowledge.py",
    THIS_REPO_ROOT / "scripts" / "test_build_knowledge_skeleton.py",
    THIS_REPO_ROOT / "scripts" / "test_index_knowledge.py",
]
SOURCE_QUERY_PY = THIS_REPO_ROOT / "graphtr-out" / "query.py"
SOURCE_BUILD_VIEWER_PY = THIS_REPO_ROOT / "graphtr-out" / "build_viewer.py"

_TARGET_SCRIPTS_PREFIX = ".claude/skills/graphtr-knowledge/scripts/"


def _rewrite_knowledge_skill_paths(text: str) -> str:
    """This repo's own graphtr-knowledge SKILL.md references its scripts at
    scripts/build_knowledge_skeleton.py and scripts/index_knowledge.py (repo
    root). A target project has no hoton-graphtr source of its own -- its copy
    of the scripts is bundled directly under the skill directory instead.
    Rewrite the doc text accordingly so a copy-pasted command in the target
    actually works."""
    text = text.replace("scripts/build_knowledge_skeleton.py", _TARGET_SCRIPTS_PREFIX + "build_knowledge_skeleton.py")
    text = text.replace("scripts/index_knowledge.py", _TARGET_SCRIPTS_PREFIX + "index_knowledge.py")
    return text


def install(target_root: Path) -> None:
    target_skills = target_root / ".claude" / "skills"

    graphtr_dst = target_skills / "graphtr"
    shutil.copytree(SOURCE_GRAPHTR_SKILL, graphtr_dst, dirs_exist_ok=True)
    print(f"installed {graphtr_dst}")

    graphtr_knowledge_dst = target_skills / "graphtr-knowledge"
    graphtr_knowledge_dst.mkdir(parents=True, exist_ok=True)
    rewritten = _rewrite_knowledge_skill_paths(SOURCE_GRAPHTR_KNOWLEDGE_SKILL_MD.read_text())
    (graphtr_knowledge_dst / "SKILL.md").write_text(rewritten)
    print(f"installed {graphtr_knowledge_dst / 'SKILL.md'}")

    scripts_dst = graphtr_knowledge_dst / "scripts"
    scripts_dst.mkdir(parents=True, exist_ok=True)
    for src in SOURCE_KNOWLEDGE_SCRIPTS:
        shutil.copy2(src, scripts_dst / src.name)
        print(f"installed {scripts_dst / src.name}")

    graphtr_out_dst = target_root / "graphtr-out"
    graphtr_out_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_QUERY_PY, graphtr_out_dst / "query.py")
    shutil.copy2(SOURCE_BUILD_VIEWER_PY, graphtr_out_dst / "build_viewer.py")
    print(f"installed {graphtr_out_dst / 'query.py'}")
    print(f"installed {graphtr_out_dst / 'build_viewer.py'}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python3 init_graphtr_skills.py <target-project-path>")
        sys.exit(1)
    target_root = Path(sys.argv[1]).resolve()
    if not target_root.is_dir():
        print(f"FAIL: target path does not exist or is not a directory: {target_root}")
        sys.exit(1)
    install(target_root)
    print(f"\nDone. Next: run mcp__hoton-graphtr__ingest_codebase for {target_root.name} per the graphtr skill's Bootstrap section.")


if __name__ == "__main__":
    main()
