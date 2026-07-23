#!/usr/bin/env python3
"""Sanity tests for init_graphtr_skills.py's pure SKILL.md path-rewrite helper."""
from init_graphtr_skills import _rewrite_knowledge_skill_paths


def test_rewrite_replaces_source_script_path_prefix():
    text = "Run `python3 scripts/build_knowledge_skeleton.py` then `python3 scripts/index_knowledge.py`."
    result = _rewrite_knowledge_skill_paths(text)
    assert result == (
        "Run `python3 .claude/skills/graphtr-knowledge/scripts/build_knowledge_skeleton.py` "
        "then `python3 .claude/skills/graphtr-knowledge/scripts/index_knowledge.py`."
    )


def test_rewrite_leaves_unrelated_text_unchanged():
    text = "See CLAUDE.md and graphtr-out/manifest.json for details."
    assert _rewrite_knowledge_skill_paths(text) == text


if __name__ == "__main__":
    test_rewrite_replaces_source_script_path_prefix()
    test_rewrite_leaves_unrelated_text_unchanged()
    print("PASS")
