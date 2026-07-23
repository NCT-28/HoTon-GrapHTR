#!/usr/bin/env python3
"""Sanity tests for build_knowledge_skeleton.py's pure text-extraction helper."""
import tempfile
from pathlib import Path

from build_knowledge_skeleton import _find_repo_root, extract_section

SAMPLE = """# Title

## Service Map

| A | B |
|---|---|
| x | y |

## Key Commands

some other content
"""


def test_extract_section_stops_at_sibling_heading():
    result = extract_section(SAMPLE, "## Service Map")
    assert "| A | B |" in result
    assert "Key Commands" not in result


def test_extract_section_missing_heading_returns_empty():
    result = extract_section(SAMPLE, "## Nope")
    assert result == ""


def test_extract_section_includes_subsections():
    text = "## Architecture\n\n### External integrations\n\n- foo\n\n## Database\n"
    result = extract_section(text, "## Architecture")
    assert "External integrations" in result
    assert "foo" in result
    assert "Database" not in result


def test_find_repo_root_walks_up_to_git_marker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".git").mkdir()
        deep = root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        assert _find_repo_root(deep) == root.resolve()


def test_find_repo_root_raises_without_git_marker():
    with tempfile.TemporaryDirectory() as tmp:
        deep = Path(tmp) / "a" / "b"
        deep.mkdir(parents=True)
        try:
            _find_repo_root(deep)
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


if __name__ == "__main__":
    test_extract_section_stops_at_sibling_heading()
    test_extract_section_missing_heading_returns_empty()
    test_extract_section_includes_subsections()
    test_find_repo_root_walks_up_to_git_marker()
    test_find_repo_root_raises_without_git_marker()
    print("PASS")
