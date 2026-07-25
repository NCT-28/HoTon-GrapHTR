#!/bin/bash
# PostToolUse:Bash — Truncate output >200 dòng, giữ head 100 + tail 50

input=$(cat)
stdout=$(echo "$input" | jq -r '.tool_response.stdout // ""')
stderr=$(echo "$input" | jq -r '.tool_response.stderr // ""')
command=$(echo "$input" | jq -r '.tool_input.command // ""')

# Bỏ qua test commands (đã có filter-test-output.sh xử lý)
if echo "$command" | grep -qE '(^|[/&|;]|\s)(npm test|npm run test|yarn test|pnpm test|npx (jest|vitest)|jest|vitest|pytest|go test|cargo test|rspec)(\s|$)'; then
  exit 0
fi

line_count=$(echo "$stdout" | wc -l)

if [ "$line_count" -le 200 ]; then
  exit 0
fi

head_part=$(echo "$stdout" | head -100)
tail_part=$(echo "$stdout" | tail -50)
truncated="$head_part
... [output truncated: $line_count lines total → showing first 100 + last 50] ...
$tail_part"

echo "{
  \"hookSpecificOutput\": {
    \"hookEventName\": \"PostToolUse\",
    \"updatedToolOutput\": {
      \"stdout\": $(echo "$truncated" | jq -Rs .),
      \"stderr\": $(echo "$stderr" | tail -50 | jq -Rs .),
      \"interrupted\": false,
      \"isImage\": false
    }
  }
}"
