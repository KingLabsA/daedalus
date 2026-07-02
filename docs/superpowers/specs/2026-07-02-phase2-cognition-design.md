# Phase 2 — Cognition: Dream, Distill, Judge, Subconscious

**Date:** 2026-07-02
**Parent spec:** `2026-07-02-hermes-deep-mind-design.md` (roadmap Phase 2)

## Goal

Make Hermes self-evolving: it mines its own experience into memory and skills, refuses to stop a goal until an independent judge is satisfied, and keeps thinking while idle.

## Components — new standalone package `core/cognition/`

Same rules as Phase 1: no imports from `agent_ultimate`, callbacks/config injected, offline by default, hook-wired, never raises into the agent loop.

**`events.py` — EventLog.** The missing substrate: tool-call history is currently in-memory only (`agent.logs`), so nothing can be mined across sessions. New `tool_events` table (session_id, tool, args signature, ok, timestamp), fed by `pre_tool`/`post_tool` hooks (call info cached on pre, outcome written on post). Exposes `sequences(max_sessions)` — per-session ordered tool-name lists.

**`dream.py` — Dreamer.** `/dream` equivalent of MiMoCode. Mines session transcripts into persistent memory:
- Heuristic pass (offline): user messages carrying corrections/preferences ("don't", "instead", "always", "prefer", "remember", …) → memory candidates.
- LLM pass (optional `llm_fn`): extract JSON `[{content, kind, importance}]` from transcript tails.
- Dedup: `difflib` similarity ≥ 0.75 against nearest FTS hit → skip.
- Prune: hard cap on total memories; lowest importance evicted first. Returns a report dict.

**`distill.py` — Distiller.** Mines `EventLog.sequences()` for repeated tool n-grams (length 2–5, support ≥ 3, not single-tool loops), packages the top candidates (max 3/run) as skills via injected `save_skill_fn` (wired to `SelfLearner.save_skill`), optional `llm_fn` for naming/description. `distilled_skills` table prevents re-saving.

**`judge.py` — GoalJudge.** `verdict(goal, messages) -> {complete, reason, confidence}`. Prompts `llm_fn` for strict JSON over the transcript tail; robust parse (extract JSON object, keyword fallback). **Fail-open:** no judge model or unparseable output → `complete=True` (never traps the agent in an infinite loop).

**`subconscious.py` — Subconscious.** Daemon thread. Hooks `post_llm`/`post_tool` poke an activity clock; after `idle_seconds` (default 180) of quiet it runs one cycle — dream (heuristics-only by default; LLM if `HERMES_SUBCONSCIOUS_LLM=on`) + distill — then waits for new activity before it may run again. Rate-limited (4 cycles/hour), disable with `HERMES_SUBCONSCIOUS=off`, logs to `.hermes/memory/subconscious.log`, `status()` for UI.

## Wiring (`agent_ultimate.py`)

- `UltimateAgent.__init__`: EventLog attached; Dreamer (llm=`_ctx_summarize`); Distiller (heuristic naming); GoalJudge (llm=`_ctx_summarize`); Subconscious started with a session-loader over `SessionStore`.
- `run_loop`: when `GoalManager.is_complete()` fires, ask the judge. Not satisfied → reset `completed`, inject `[JUDGE] Goal NOT satisfied: <reason>. Continue.` and keep looping. Satisfied (or fail-open) → stop as before.
- Tools: `dream_now`, `distill_now`, `subconscious_status`. CLI: `/dream`, `/distill`, `/subconscious`. WS commands: `dream`, `distill`, `subconscious`.

## Testing

`tests/test_cognition.py`, offline (fake `llm_fn`s, tmp DBs, sub-second subconscious thresholds): event recording + per-session sequences; distill support threshold, dedup across runs, single-tool-loop exclusion; dream heuristic extraction, LLM-JSON path, dedup, prune; judge true/false/garbage/absent verdicts; subconscious idle trigger, poke reset, one-cycle-per-idle-period, env kill-switch; never-raise on garbage. Existing suites stay green.
