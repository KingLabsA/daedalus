# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in Daedalus, please report it responsibly:

- **Email**: Create a GitHub issue with the `security` label
- **Do NOT** open public issues for security vulnerabilities

## Scope

Daedalus executes code on the user's machine. The following are in scope:

- Command injection via tool arguments
- Prompt injection that bypasses safety controls
- Arbitrary file read/write outside the workspace
- API key leakage through logs, error messages, or telemetry
- WebSocket unauthorized access
- Path traversal in file operations

## Out of Scope

- Vulnerabilities in upstream LLM providers (OpenAI, Anthropic, etc.)
- Issues requiring physical access to the machine
- Social engineering attacks

## Security Measures

- Blocked command list for `run_command` (not comprehensive — use Docker sandbox for untrusted code)
- Prompt injection pattern matching (basic — do not rely on as sole defense)
- WebSocket token authentication (`HERMES_WS_TOKEN`)
- Git-based checkpoints for rollback
- `safe_repo_path()` prevents path traversal in file writes

## Recommendations

- Run in Docker sandbox for untrusted workloads
- Use `SAFETY_MODE=suggest` for interactive use
- Set `HERMES_WS_TOKEN` when exposing the WebSocket server
- Never run with `SAFETY_MODE=auto` on untrusted codebases
