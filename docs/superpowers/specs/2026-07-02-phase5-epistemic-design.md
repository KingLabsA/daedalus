# Phase 5 — Epistemic Engine: Calibration, Cost-Aware Routing, Max Mode

**Date:** 2026-07-02
**Parent spec:** `2026-07-02-hermes-deep-mind-design.md` (Phase 5)

## Goal

Give Hermes calibrated self-knowledge: it learns how often it is actually right when it feels confident, routes work to cheap or strong models based on that learned calibration plus task difficulty, and can burn compute deliberately (judged best-of-N) when the stakes demand it.

## Package — `core/epistemic/` (standalone, stdlib-only, injectable)

**`calibration.py` — CalibrationTracker.** The part nobody ships: predictions vs reality, persisted.
- `record(kind, confidence, success)` → SQLite `calibration_events` (kind: goal_judge, tool_run, max_mode, …).
- `calibrated(confidence, kind=None)` → adjusted probability from bucketed history (10 buckets, Laplace-smoothed, falls back to raw confidence with <5 samples per bucket).
- `report()` → per-bucket predicted-vs-actual with sample counts (the calibration curve).
- Outcome feeds (wired in agent): every tool call records `tool_run` success/failure (hook-driven); every goal-judge verdict records its confidence, resolved by whether the goal later re-fails.

**`router.py` — CostAwareRouter.** Expected-value routing across providers:
- Static cost tiers per provider (cheap=1 … premium=4; local=0).
- `difficulty(prompt)` heuristic 0–1: length, reasoning keywords, code-refactor markers, multi-step cues.
- `route(prompt)` → provider + reason: low difficulty & high calibrated success at cheap tier → cheapest live provider; high difficulty or poor cheap-tier calibration → strongest live provider (fable/anthropic/openai head). Returns `{provider, tier, difficulty, reason}`.
- Learns: routing outcomes recorded to the tracker under kind `route_tier_<n>`; calibration per tier feeds back into the threshold.

**`maxmode.py` — MaxMode.** Judged best-of-N (vs Phase 3 committee's merge):
- Generate N candidate answers from distinct providers (via injected `candidates_fn` — wired to orchestra members).
- Judge scores each 0–10 against the prompt (strict JSON, robust parse, fail-open to longest answer).
- Returns winner, scores, and records the judge's implied confidence to the tracker.

## Wiring (`agent_ultimate.py`)

- `UltimateAgent.__init__`: tracker (shared DB), router (live providers + orchestra classify), max mode (orchestra committee members + `_ctx_summarize` judge).
- Hooks: `post_tool` results → `tool_run` outcomes (success = no error marker).
- `run_loop` goal judging: verdict confidence recorded; `complete=False` verdicts count as correct predictions when work continued (proxy: rejection accepted).
- Tools: `max_mode(prompt, n)`, `route_task(prompt)` (explains routing), `calibration_report()`.
- CLI `/max <prompt>`, `/route <prompt>`, `/calibration`. WS: `calibration`, `route:<prompt>`.

## Testing

`tests/test_epistemic.py`, offline: bucket math + Laplace smoothing + insufficient-data fallback; difficulty heuristic ordering; routing decisions cheap-vs-strong under fake calibration histories; max mode picks judge-preferred candidate, survives garbage judge output, records calibration. Existing 142 stay green.
