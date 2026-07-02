# Phase 4 — Platform: MCP Client, Device Doctor, Profile Builder, Model Advisor, Fable 5

**Date:** 2026-07-02
**Parent spec:** `2026-07-02-hermes-deep-mind-design.md` (Phase 4, plus user-requested expansion: dependency scanning, first-launch profiling, hardware-aware model listing, Fable 5)

## Goal

Make Hermes a first-class citizen of its environment: speak MCP natively, know what's installed (and missing) on the machine, adapt itself to who the user is on first launch, and recommend exactly which models this hardware can run.

## Package — `core/platform/` (standalone, stdlib-only, injectable, never raises into the loop)

**`mcp_client.py` — McpClient.** Real Model Context Protocol client, stdio transport (newline-delimited JSON-RPC 2.0): spawns servers from `.hermes/mcp.json` (`{"servers": {name: {command, args, env}}}`), performs the `initialize` handshake, then `tools/list` and `tools/call`. Per-server reader thread + response queue with timeouts; connections cached; `close_all()` on shutdown. Replaces the fake `mcp_call` tool (which shelled out to a nonexistent `mcp` CLI).

**`doctor.py` — DependencyScanner.** Scans the device Hermes is installed on:
- binaries (git, docker, node, npm, ffmpeg, sox, pyright, ollama, tsc…) with per-feature impact ("video analysis needs ffmpeg") and install hints (brew/npm/pip);
- Python packages the agent uses (openai, anthropic, websockets, playwright, pyautogui…);
- provider env keys (which of the 21 are live);
- disk free space.
Report = `{ok: [...], missing: [{name, needed_for, install}], providers: {...}}` + `fix_script()` emitting copy-paste install commands. `shutil.which`/runner injectable for tests.

**`profiler.py` — ProfileBuilder.** First-launch onboarding: ~5 questions (role, domains, tech/languages, experience, goals) via injectable `ask_fn`. Personas: developer, project_manager, doctor_medical, engineer, data_scientist, researcher, designer, writer, student, business. Each persona ships base **skill packs** (markdown skills via injected `save_skill_fn`), **memory seeds** (preferences into MemoryStore), and a **system-prompt addendum**. Profile persisted to `.hermes/profile.json`; rebuildable with `/profile rebuild`. CLI runs it automatically when no profile exists (skippable with Enter).

**`modeladvisor.py` — ModelAdvisor** (the "odysseus" feature). Detects machine specs (RAM via sysctl/meminfo, CPU cores, Apple Silicon, nvidia-smi presence) and produces a tiered answer to "what can I actually run here?":
- **local**: catalog of Ollama-tier models with RAM requirements (3B→70B quants), filtered to what fits, flagged `installed` when `ollama list` already has them (includes the user's own mythos/hermes models);
- **cloud**: models reachable through configured provider keys;
- **recommended**: best local + best cloud picks for this machine.
Spec detection and ollama listing injectable.

## Fable 5

New provider entry `fable` (Anthropic lib, `ANTHROPIC_API_KEY`): default model `claude-fable-5`, models list includes `claude-opus-4-8`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`. Orchestra profiles updated so `fable` leads the reasoning/creative expert lists when available.

## Wiring (`agent_ultimate.py`)

- `UltimateAgent.__init__`: McpClient, DependencyScanner, ModelAdvisor, ProfileBuilder (wired to context store + SelfLearner.save_skill); loads `.hermes/profile.json` and appends the persona addendum to the system prompt.
- First launch (CLI `chat()`): no profile → doctor summary + interview + model advisor, all skippable.
- Tools: `mcp_servers`, `mcp_tools`, `mcp_invoke` (and legacy `mcp_call` rewired to the real client), `system_doctor`, `advise_models`, `show_profile`.
- CLI: `/doctor`, `/models`, `/profile [rebuild]`, `/mcp <list|tools|call> …`. WS: `doctor`, `advisor`, `profile`, `mcp:tools:<server>`.

## Testing

`tests/test_platform.py`, offline: MCP client against a scripted fake JSON-RPC stdio server (initialize/tools-list/tools-call/timeout); doctor with fake which/runner (missing-dep hints, fix script); profiler interview → skills+seeds+profile.json for two personas; advisor tier filtering by fake RAM sizes + installed-model flagging; fable provider config presence. Existing 124 stay green.
