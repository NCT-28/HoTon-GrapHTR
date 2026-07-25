# Verification via Sandbox

**NEVER say "code looks correct" without executing it. Seeing is not verifying.**

After any bug fix or logic change, verify by running the code — not by reading it.

## Required steps

1. Identify the appropriate test/run command for the language and project
2. Run it via Bash directly (not `ctx_batch_execute`) — the repo's `filter-test-output.sh`/`truncate-bash-output.sh` PostToolUse hooks only intercept the Bash tool, and they already handle filtering failures and truncating long output for test commands. Routing test runs through context-mode instead would bypass those hooks and duplicate their filtering logic.
3. Parse the output: determine PASS or FAIL with evidence from the output
4. Only claim the fix is complete if the result is PASS

**If no test exists:** write a minimal test that reproduces the bug first, then run it.

**If the test command itself needs external gathering** (e.g. correlating results across multiple log files or runs), use `ctx_batch_execute`/`ctx_search` for that analysis step, but keep the actual test invocation on Bash.

## Patterns to avoid

- "The fix looks correct" → WRONG
- "Compiled successfully" alone → WRONG (compile ≠ logic correct)
- Running the test and reporting its output → CORRECT
