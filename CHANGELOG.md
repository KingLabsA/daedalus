# Changelog

## 1.2.0 — "Ship-ready CLI + Web" (2026-07-03)

Turned the deep-brain agent into a usable product across terminal and browser.

### Phase 8 — Product surfaces
- `hermes` launcher (`pip install -e .`): `tui` (default) / `web` / `ws` / `doctor` / `models`
- Rich terminal TUI; self-served web IDE (Python serves built frontend, one process)
- WebSocket token auth (`HERMES_WS_TOKEN`); `handle_command` extracted (UI-agnostic)

### Phase 9 — Reliability (local-first, free-only)
- `freellmapi` provider (localhost:3002 gateway, 67 free models); added to router + orchestra
- Validated provider liveness (probes `/models`, not just key presence; 5-min cache)
- Local guardrails: 16-tool `CORE_TOOLS` for local models, `num_ctx` cap, per-call timeout
- Retry-once + provider failover inside the loop; `MODEL_NAME` scoped to selected provider
- Token streaming (`agent.on_token`); `install.sh`, `QUICKSTART.md`, valid build backend
- First verified live agent round-trip

### Phase 10 — Conversation UX
- Multi-turn continuity (`converse()` keeps context across turns; `/reset` clears)
- Diff-approve: colored diff/command preview + y/N/a before destructive tools; denials feed back
- Rich TUI input history (up-arrow, persisted), per-turn Ctrl-C interrupt

**Stats**: 199 tests · 23 providers · local-first auto-routing · terminal + web surfaces

## 1.0.0 — "Deep Mind" (2026-07-02)

Six-phase evolution from autonomous coding agent to a self-evolving, multimodal,
epistemically-calibrated coding assistant. Full design specs in `docs/superpowers/specs/`.

### Phase 1 — Context Engine (`core/context`)
- Persistent cross-session memory (SQLite FTS5, ranked by relevance × importance)
- Budgeted context injection; checkpoint-based context **reconstruction** (not just truncation)
- **Failure Immune System**: past failures stored as antibodies, injected before similar actions
- Tools: `remember`, `recall_memory`, `memory_stats` · CLI `/memory`, `/remember`

### Phase 2 — Cognition (`core/cognition`)
- **Dream**: mines session transcripts into memory (heuristic + LLM, dedup, pruning)
- **Distill**: mines repeated tool workflows from the new persistent event log into skills
- **GoalJudge**: independent judge verifies `/goal` completion (fail-open)
- **Subconscious**: sleep-time compute — dream+distill cycles while idle
- Tools: `dream_now`, `distill_now`, `subconscious_status` · CLI `/dream /distill /subconscious`

### Phase 3 — Intelligence & Senses (`core/intel`, `core/senses`)
- CodeIntel (ast/regex symbols, definitions, references, diagnostics), TF-IDF semantic search
- **Causal World Model**: git co-change + import fan-in → blast-radius prediction, with a
  sentinel that warns the agent before high-risk edits
- **ModelOrchestra** (agent-level MoE): task classification → expert provider routing;
  parallel committees with synthesis
- Vision (image + ffmpeg-sampled video), VoiceIO (ASR, mic listen, TTS)
- CLI `/blast /experts /see /say /listen`

### Phase 4 — Platform (`core/platform`)
- Real **MCP client** (stdio JSON-RPC): `.hermes/mcp.json`, handshake, tools/list, tools/call
- **Device doctor**: scans binaries/python packages/provider keys with fix script
- **Profile builder**: first-launch interview → 10 personas, pre-built skill packs, memory seeds
- **Model advisor**: hardware detection → tiered local model recommendations + cloud catalog
- **Fable 5** provider (`claude-fable-5`, Claude 5 family); leads reasoning expert profiles
- CLI `/doctor /models /profile /mcp`

### Phase 5 — Epistemic Engine (`core/epistemic`)
- **CalibrationTracker**: predicted confidence vs actual outcomes, learned per environment
- **CostAwareRouter**: easy→cheapest live provider, hard→strongest; thresholds adapt to
  learned tier success rates
- **Max Mode**: judged best-of-N across distinct providers
- CLI `/max /route /calibration`

### Phase 6 — Deep Mind UI (`desktop/`)
- **Mind tab**: memory, subconscious, calibration, expert routing, blast radius, doctor,
  model advisor, profile
- **Onboarding wizard** on first app launch

### Phase 7 — Integration
- **Auto-routing live in the agent loop**: every run cost-routes to the right provider
  (falls back to your provider on failure; `/provider` pins; `HERMES_AUTO_ROUTE=off` disables)
- Fixed WS `provider:test:` shadowing bug; README rewritten; version 1.0.0

**Stats**: 83 tools · 22 providers · 6 standalone core packages · 160+ offline tests
