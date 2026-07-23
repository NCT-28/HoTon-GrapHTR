#!/usr/bin/env python3
"""Sanity tests for build_knowledge_skeleton.py's pure text-extraction helper."""
from build_knowledge_skeleton import extract_section

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


if __name__ == "__main__":
    test_extract_section_stops_at_sibling_heading()
    test_extract_section_missing_heading_returns_empty()
    test_extract_section_includes_subsections()
    print("PASS")
