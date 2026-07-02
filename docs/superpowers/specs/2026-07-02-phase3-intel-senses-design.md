# Phase 3 — Code Intelligence, Causal World Model, Senses & Model Orchestra (MoE)

**Date:** 2026-07-02
**Parent spec:** `2026-07-02-hermes-deep-mind-design.md` (Phase 3, plus user-requested expansion: MoE, image, video, voice)

## Goal

Give Hermes real understanding of code (symbols, diagnostics, semantic search), foresight about edits (causal blast-radius prediction), multimodal senses (image/video/voice), and agent-level mixture-of-experts routing across its 21 providers.

## Package 1 — `core/intel/` (code intelligence + world model)

Same architecture rules as Phases 1–2: standalone, dependency-light (stdlib only), injectable, hook-wired, never raises into the loop.

**`codeintel.py` — CodeIntel.** Symbols/definitions/references without heavyweight LSP servers: Python via `ast` (functions, classes, methods with parent prefixes), JS/TS via regex. `find_definition(name)` scans the tree (skipping `.git`, `node_modules`, `__pycache__`, `dist`, venvs); `references(name)` word-boundary scan with caps. `diagnostics(path)`: pyright JSON if installed, else stdlib `py_compile` sweep; tsc `--noEmit` if available.

**`semsearch.py` — SemanticIndex.** Pure-Python TF-IDF over code chunks (~40 lines each, camelCase/snake_case-aware tokenization, cosine similarity). Honest semantic-lite: no embedding model dependency, works offline, strictly better than the existing keyword indexer. Lazy build on first search; capped file count.

**`worldmodel.py` — CausalWorldModel.** Mined from `git log --name-only` (last 500 commits, mega-commits capped): co-change strengths between files, plus Python import fan-in via `ast`. `blast_radius(file)` → risk score (0–1) from co-change degree + fan-in, with the likely-affected files and reasons. `render_warning(file)` → empty below risk 0.3.

**`sentinel.py` — WorldModelSentinel.** The foresight wiring: `pre_tool` watches write tools (`write_file`, `edit_file_line`, `append_file`) and computes blast radius for targets; `pre_llm` injects pending warnings into the system message under its own `<!--HERMES:WM-->` markers (never touching the ContextEngine's block), then clears them.

## Package 2 — `core/senses/` (MoE + multimodal)

**`orchestra.py` — ModelOrchestra.** Agent-level mixture-of-experts:
- `classify(prompt)` → task type (code, reasoning, vision, cheap, creative, long_context) via keyword heuristics.
- `pick(type)` → first *available* provider in that type's expert profile (availability = provider's env key set, or local like ollama/hermes); profiles overridable.
- `consult(prompt, type)` → route one prompt to the right expert.
- `committee(prompt, n)` → fan out to n distinct experts in parallel (threads), then a reasoning-expert synthesizes the answers. This is Max Mode's foundation (Phase 5 adds judged best-of-N + calibration).
`call_fn(provider, prompt)` and `available_fn()` injected — no provider code imported.

**`vision.py` — Vision.** `analyze_image(path, question)` builds OpenAI-style `image_url` data-URL content parts for an injected `vision_chat_fn`. `analyze_video(path, question)` extracts up to N evenly-sampled frames via `ffmpeg` (injectable extractor), describes each frame, then synthesizes a single answer. Vision provider/model via `HERMES_VISION_PROVIDER`/`HERMES_VISION_MODEL` (defaults to active provider).

**`voice.py` — VoiceIO.** `transcribe(path)` (injected ASR fn → OpenAI-compatible `audio.transcriptions`, `HERMES_ASR_*` env), `listen(seconds)` (records via `sox`/`ffmpeg`, whichever exists, then transcribes), `speak(text)` (injected TTS fn → `audio.speech`, plays via `afplay` on macOS). Graceful "not available" strings when binaries/keys are missing — never crashes.

## Wiring (`agent_ultimate.py`)

- Module helpers: `_plain_llm_call(provider, prompt)` (ProviderRouter, no tools), `_available_providers()` (env-key check), `_vision_call(messages)`, `_asr_call(path)`, `_tts_call(text)`.
- `UltimateAgent.__init__`: CodeIntel, SemanticIndex, CausalWorldModel + attached Sentinel, ModelOrchestra, Vision, VoiceIO.
- New tools (13): `code_symbols`, `find_definition`, `find_references`, `semantic_search`, `build_world_model`, `predict_blast_radius`, `consult_expert`, `expert_committee`, `analyze_image`, `analyze_video`, `transcribe_audio`, `listen`, `speak`.
- CLI: `/blast <file>`, `/experts [prompt]`, `/see <image> [question]`, `/say <text>`, `/listen [seconds]`. WS: `experts`, `blast:<path>`.

## Testing

`tests/test_intel.py` + `tests/test_senses.py`, offline (fake call/chat/asr/tts fns, temp git repos built in-test, fake frame extractor): symbols/definitions/references; TF-IDF relevance ordering; co-change mining from a scripted git history; blast-radius risk ordering and sentinel injection idempotency; orchestra classify/pick/availability-fallback/committee synthesis; vision data-URL message shape; video frame sampling + synthesis; voice graceful degradation. Existing 95 stay green.

## Out of scope

Real LSP servers over stdio, embedding-model search, MCP client (Phase 4), judged best-of-N with calibration (Phase 5), UI (Phase 6).
