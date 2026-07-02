# Phase 8 — Product Surfaces: `hermes` Launcher, Rich Terminal CLI, Web App IDE

**Date:** 2026-07-02
**Decision:** desktop (Tauri) parked until upstream tao/macOS-26 fix; focus on CLI + web.

## Goal

Turn the dev setup into a product: one `hermes` command that is either a rich terminal
CLI or a self-served web IDE — no Node at runtime, no two-terminal dance, authenticated by default.

## Components

**`hermes_cli.py` (new, repo root) + `[project.scripts] hermes`** — entry point with subcommands:
- `hermes` / `hermes tui` — rich terminal UI: banner (provider, model, tools, memory stats),
  markdown-rendered responses with syntax-highlighted code, spinner while the agent works,
  colored slash-command output. Uses `rich` when available; degrades to the plain loop without it.
- `hermes web [--port]` — the web IDE: builds `desktop/dist` on demand if missing (via npm)
  then serves it from **Python's http.server** (SPA fallback), starts the WS agent server in a
  thread, generates a session token, injects it into the served HTML, and opens the browser.
  One process, one command.
- `hermes ws` — headless agent server (existing behavior).
- `hermes doctor` / `hermes models` / `hermes version` — one-shot utilities.

**Command extraction (`UltimateAgent.handle_command`)** — the ~27 slash-command handlers move
out of `chat()`'s print-loop into a UI-agnostic method returning output strings. `chat()` and the
rich TUI both delegate to it; the web/WS layer stays as is. This removes the CLI/TUI duplication risk.

**WebSocket auth** — when `HERMES_WS_TOKEN` is set (the launcher sets it to `secrets.token_hex(16)`),
the WS server rejects connections whose URL lacks `token=<value>` (close code 4401). Frontend reads
`window.HERMES_TOKEN` (injected by the launcher) and appends it to the WS URL. Dev mode
(`npm run dev`, no env token) is unchanged — auth is on exactly when the launcher runs it.

## Packaging

`pyproject.toml`: `[project.scripts] hermes = "hermes_cli:main"`, py-modules include
`agent_ultimate` + `hermes_cli`, packages include `core*`; `rich` added to requirements.
Version 1.1.0. `pip install -e .` gives a global `hermes` command.

## Testing

`tests/test_launcher.py` (offline): token injection into HTML (idempotent, correct placement);
`ws_token_ok()` gate (no requirement → open; requirement → exact token in query only);
dist detection; `handle_command` extraction smoke (a few commands return strings and `chat` parity).
Frontend: tsc + vite build stay green.
