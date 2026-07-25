#!/bin/bash
# SessionStart:startup — Inject project context, tránh Claude tự grep/ls để hiểu project

branch=$(git branch --show-current 2>/dev/null)
recent=$(git log --oneline -5 2>/dev/null | sed 's/^/  /')
modified=$(git diff --name-only HEAD 2>/dev/null | head -10 | tr '\n' ' ')
staged=$(git diff --cached --name-only 2>/dev/null | head -5 | tr '\n' ' ')

echo "## Project Context (auto-injected at session start)
- Branch: ${branch:-unknown}
- Recent commits:
$recent
- Modified (unstaged): $modified
- Staged: $staged
- Key files: CLAUDE.md (project instructions)"
