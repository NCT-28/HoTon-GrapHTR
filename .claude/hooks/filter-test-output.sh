#!/bin/bash
# PostToolUse:Bash — Filter test output, chỉ giữ failures (chỉ chạy cho test commands)

input=$(cat)
stdout=$(echo "$input" | jq -r '.tool_response.stdout // ""')
stderr=$(echo "$input" | jq -r '.tool_response.stderr // ""')
command=$(echo "$input" | jq -r '.tool_input.command // ""')

# Chỉ filter khi là test command — các lệnh khác bỏ qua
if ! echo "$command" | grep -qE '(^|[/&|;]|\s)(npm test|npm run test|yarn test|pnpm test|npx (jest|vitest)|jest|vitest|pytest|go test|cargo test|rspec)(\s|$)'; then
  exit 0
fi

line_count=$(echo "$stdout" | wc -l)

# Chỉ filter khi output đủ dài
if [ "$line_count" -lt 30 ]; then
  exit 0
fi

# Pattern includes Rust panics ('panicked at'), JS/TS errors, and common failure keywords
filtered=$(echo "$stdout" | grep -E '(FAIL|ERROR|✗|×|FAILED|Error:|assert|Traceback|failed [0-9]|panicked at|PANICKED|thread .* panicked)' | head -50)

# Include stderr failures (Rust writes panics to stderr in some configurations)
stderr_filtered=$(echo "$stderr" | grep -E '(FAIL|ERROR|panicked at|thread .* panicked|error\[)' | head -20)

if [ -z "$filtered" ] && [ -z "$stderr_filtered" ]; then
  filtered="All tests passed."
fi

combined="${filtered}${stderr_filtered:+$'\n'$stderr_filtered}"

jq -n \
  --arg stdout "$combined" \
  --arg stderr "" \
  '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      updatedToolOutput: {
        stdout: $stdout,
        stderr: $stderr,
        interrupted: false,
        isImage: false
      }
    }
  }'
