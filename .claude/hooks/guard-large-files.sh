#!/bin/bash
# PreToolUse:Read — Files >1000 dòng: hỏi trước, gợi ý grep/offset thay vì đọc full

input=$(cat)
file=$(echo "$input" | jq -r '.tool_input.file_path // ""')

if [ -f "$file" ]; then
  size=$(wc -l < "$file")
  if [ "$size" -gt 1000 ]; then
    echo "{
      \"hookSpecificOutput\": {
        \"hookEventName\": \"PreToolUse\",
        \"permissionDecision\": \"ask\",
        \"permissionDecisionReason\": \"File '$file' has $size lines. Prefer Bash+grep or Read with offset/limit. Allow only if you need the full file (e.g. to Edit it).\"
      }
    }"
    exit 0
  fi
fi

exit 0
