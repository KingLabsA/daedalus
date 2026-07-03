# Phase 9 — Reliability & Distribution (local-first, free-only)

**Date:** 2026-07-03
**User constraints:** rely on local Ollama models first; FreeLLMAPI (localhost:3002, must be
launched) as the gateway to 67 models; if cloud APIs are used, free tiers only — never paid.

## Problems found in live testing (2026-07-03)

1. `ANTHROPIC_API_KEY` holds a FreeLLMAPI key → 401; provider "live" check tests key
   *presence*, not validity → false positives, hangs.
2. No FreeLLMAPI provider entry despite it being the user's own gateway.
3. Local chat unusable: hermes3:8b loaded 22 GB (131k ctx) on a 24 GB machine, and all 83
   tool schemas are sent to every model.
4. `MODEL_NAME` env, when set, overrides the model for **every** provider (breaks routing).
5. No timeouts, no retries, no failover in `ProviderRouter`.
6. Invalid `build-backend` string in pyproject; no installer or quickstart.

## Fixes

**Providers.** New `freellmapi` entry (`FREELLMAPI_HOST` default `http://localhost:3002`,
key via `FREELLMAPI_API_KEY`, `local: true`). `ollama`/`hermes` marked `local: true` +
`ollama: true`. `_model_for(provider)`: `MODEL_NAME` only applies to the user's selected
provider; routed providers always use their own default.

**Liveness (`_provider_alive` / `_live_providers`).** Replaces presence-only checks for
routing and the orchestra: local providers probed via `GET {base}/models` (2 s timeout);
OpenAI-compatible clouds via authenticated `GET /models` (4 s, free call); anthropic via
`/v1/models`; google/cohere fall back to key presence. Probed concurrently, cached 5 min.
Doctor and `hermes doctor` show *live now* vs *key set*.

**Local guardrails.** For `local` providers: tool schemas pruned to ~14 `CORE_TOOLS`
(read/write/edit/run/grep/git/search/memory/task) instead of 83; Ollama-backed calls get
`extra_body.options.num_ctx` (default 8192, `HERMES_LOCAL_NUM_CTX`) so an 8B model stops
ballooning to 22 GB. All openai-lib calls get `timeout` (`HERMES_LLM_TIMEOUT`, default 120 s).

**Hardening.** `run_loop` LLM calls: 1 retry (1.5 s backoff) on the same provider, then
failover — routed→user's provider (existing), user's provider→next live provider — before
giving up. Failures recorded to calibration.

**Streaming.** `agent.on_token` callback: when set, `run_loop` uses `call_stream` and emits
tokens live. The rich TUI sets it → tokens render as they arrive, final answer re-rendered
as markdown.

**Distribution.** `build-backend = "setuptools.build_meta"`; `install.sh` (checks python,
pip-installs editable, verifies `hermes version`); `QUICKSTART.md`. PyPI upload deferred
(needs the user's PyPI account).

## Testing

`tests/test_reliability.py` (offline, monkeypatched ProviderRouter/requests): freellmapi
config + local flags; model override scoping; tool pruning for local vs cloud; retry-once
then success; failover to next live provider; liveness cache TTL + probe logic; on_token
streaming assembly. Suite stays green.
