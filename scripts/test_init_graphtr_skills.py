#!/usr/bin/env python3
"""Sanity tests for init_graphtr_skills.py's pure SKILL.md path-rewrite helper."""
from init_graphtr_skills import _rewrite_knowledge_skill_paths


def test_rewrite_replaces_source_script_path_prefix():
    text = "Run `python3 hoton-rag/scripts/build_knowledge_skeleton.py` then `python3 hoton-rag/scripts/index_knowledge.py`."
    result = _rewrite_knowledge_skill_paths(text)
    assert "hoton-rag/scripts/" not in result
    assert ".claude/skills/graphtr-knowledge/scripts/build_knowledge_skeleton.py" in result
    assert ".claude/skills/graphtr-knowledge/scripts/index_knowledge.py" in result


def test_rewrite_leaves_unrelated_text_unchanged():
    text = "See CLAUDE.md and graphtr-out/manifest.json for details."
    assert _rewrite_knowledge_skill_paths(text) == text


if __name__ == "__main__":
    test_rewrite_replaces_source_script_path_prefix()
    test_rewrite_leaves_unrelated_text_unchanged()
    print("PASS")
