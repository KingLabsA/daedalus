# Hermes Deep Mind — Design Spec

**Date:** 2026-07-02
**Goal:** Evolve Hermes Ultimate into the most advanced coding assistant — beyond OpenCode, MiMoCode, and Cursor — by adding the agent-intelligence layers they have, plus four cognitive systems no shipped assistant has.

## Context

Hermes Ultimate already has: 21 providers, 55 tools, subagents, kanban orchestration, git checkpoints, safety modes, lifecycle hooks, skill recording, Docker sandbox, browser/desktop control, a WebSocket server, and a React frontend (`desktop/`).

Reality check from code audit:
- All logic lives in `agent_ultimate.py` (2,223 lines); `core/*.py` are one-line re-export stubs.
- `compress_messages` (the only context management) is **never called** — dead code.
- `core/__init__.py` eagerly imports from `agent_ultimate`, so any `core` package that `agent_ultimate` imports back would be circular. Must be made lazy (PEP 562).
- CLI `chat()` rebuilds `messages` fresh every turn — no in-session continuity; WS path appends properly.
- Hook events available: `on_start`, `pre_llm`, `post_llm`, `pre_tool`, `post_tool`, `on_error`, `on_stop`.

Benchmark: MiMoCode (OpenCode fork) adds persistent FTS5 memory, checkpoint-based context reconstruction, budgeted injection, tree tasks, judge-verified `/goal` stops, compose workflows, `/dream` + `/distill` self-evolution, Max Mode (best-of-N + judge).

## Roadmap (approved order)

| Phase | Deliverable | Novel addition ("deeper side of AI") |
|-------|------------|--------------------------------------|
| 1 | **Context Engine + Structured Memory** | **Immune System** — failure antibodies |
| 2 | Dream/Distill + judge-verified goals | **Subconscious** — sleep-time compute |
| 3 | LSP + semantic code intelligence | **Causal World Model** — blast-radius prediction |
| 4 | MCP client support | — |
| 5 | Max Mode + cost-aware routing | **Epistemic Engine** — learned confidence calibration |
| 6 | UI overhaul in `desktop/` | surfaces all of the above |

Each phase is its own spec → plan → implementation cycle. This spec details **Phase 1**.

## Phase 1 — Context Engine, Structured Memory, Immune System

### Principles
- New code lives in a real package: `core/context/` — no additions to the monolith beyond thin wiring.
- `core/context` imports **nothing** from `agent_ultimate` (config passed in constructor). `core/__init__.py` becomes lazy so `core.context` is importable standalone.
- No network required by default: LLM summarization is an injectable `summarize_fn`; heuristics used when absent. Tests run offline.
- Wire-in via existing `HookManager` events only.

### Components (`core/context/`)

**`store.py` — MemoryStore.** SQLite (same DB file as SessionStore by default) with FTS5:
- `mem_entries(id, kind, content, importance, uses, created_at, last_used)` + `mem_fts` (FTS5, trigger-synced). Kinds: `project`, `decision`, `note`, `preference`.
- `ctx_checkpoints(id, session_id, data JSON, created_at)`.
- `failures(id, tool, signature, error, remedy, hits, created_at, last_seen)` + `fail_fts`. Deduped by `(tool, signature)` — repeat failures bump `hits`.
- Ranked search: BM25 × (0.5 + importance), recency as tiebreak; FTS queries sanitized (tokens quoted).
- Mirrors human-readable files under `.hermes/memory/`: `MEMORY.md` (top entries by importance), `checkpoint.md` (latest checkpoint).

**`budgeter.py` — TokenBudgeter.** Char/4 token estimation; `max_context_tokens` (env `HERMES_MAX_CONTEXT_TOKENS`, default 32k) minus output reserve (4k); `over_budget(messages)` at 75% of budget; proportional `allocate(weights, total)`; `clip(text, tokens)`.

**`checkpointer.py` — Checkpointer.** Builds a structured checkpoint dict from messages: `goal` (first user msg), `last_request`, `files_touched` (paths extracted from tool traffic), `tools_used` (counts), `last_assistant`, `summary` (via `summarize_fn` when provided, else empty). Persists via store; renders `checkpoint.md`.

**`immune.py` — ImmuneSystem.** Caches calls from `pre_tool`; on `post_tool`, results containing `Error`/`ToolError` are recorded as antigens (`tool` + arg signature + error). `antibodies_for(messages)` searches failures relevant to the current user intent + recent tools and renders a warning block: *"You previously failed at X this way — avoid it."*

**`engine.py` — ContextEngine.** Facade owning the above. `attach(HookManager)` registers:
- `pre_llm` → inject a delimited context block (`<!--HERMES:CTX:BEGIN/END-->`) into the system message (idempotent regex replace): relevant memories + antibodies + resume checkpoint, each budget-capped. Then, if over budget → **reconstruct**: write checkpoint, replace `messages` in place with `[system, checkpoint-restore message, recent tail]` (tail never starts on a `tool` message).
- `pre_tool`/`post_tool` → immune system observation.
- `on_stop` → write session checkpoint.

Public API: `remember(content, kind, importance)`, `recall(query, k)`, `stats()`.

### Wiring (`agent_ultimate.py`, minimal diff)
- `UltimateAgent.__init__`: create `self.context = ContextEngine(db_path=DB_FILE, session_id=self.session_id, summarize_fn=<cheap provider call>)`; `attach(HookManager)`.
- New tools: `remember`, `recall_memory`, `memory_stats` (str-arg idiom like existing tools).
- CLI: `/memory [query]`, `/remember <text>`. WS command: `memory` → stats JSON.
- System prompt gains one line telling the model it has persistent memory and should `remember` important decisions/facts.

### Testing
`tests/test_context_engine.py` (pytest, offline, tmp DB per test, hooks detached after each):
memory CRUD + ranked FTS search; failure dedup + antibody retrieval; budgeter estimation/allocation/over-budget; checkpoint build/persist/render; pre_llm injection idempotency; reconstruction shrinks oversized conversations and never leads with a `tool` message; `core.context` importable without importing `agent_ultimate`. Existing `tests/test_core.py` must still pass (guards the lazy `core/__init__` change).

### Error handling
Engine failures must never break the agent loop: every hook handler wrapped, errors logged to `.hermes/memory/engine_errors.log`, agent continues.

### Out of scope (later phases)
Dream/distill, subconscious background loop, judge-verified goals, LSP, MCP, Max Mode, epistemic calibration, UI work.
