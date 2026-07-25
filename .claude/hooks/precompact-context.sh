#!/bin/bash
# PreCompact — Inject danh sách files modified vào context trước khi summarize

modified=$(git diff --name-only 2>/dev/null | head -20 | tr '\n' ' ')
staged=$(git diff --cached --name-only 2>/dev/null | head -10 | tr '\n' ' ')
branch=$(git branch --show-current 2>/dev/null)

jq -n \
  --arg branch "$branch" \
  --arg modified "$modified" \
  --arg staged "$staged" \
  '{
    hookSpecificOutput: {
      hookEventName: "PreCompact",
      additionalContext: "PRESERVE IN SUMMARY — Branch: \($branch). Unstaged changes: \($modified). Staged: \($staged)."
    }
  }'
