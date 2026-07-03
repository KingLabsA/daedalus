# Hermes Quickstart

## Install (once)

```bash
git clone <this-repo> && cd supercoding   # or wherever it lives
./install.sh                              # installs the global `hermes` command
```

## Run

```bash
hermes doctor     # what's missing on this machine + which providers are LIVE right now
hermes            # rich terminal UI (streams tokens, renders markdown)
hermes web        # web IDE at http://127.0.0.1:8899 (one process, token-protected)
hermes models     # which local/cloud models this hardware can run
```

## Models — local-first, free-only

Hermes routes every request automatically (easy → free local model, hard → strongest live
provider) and **only uses providers that actually answer** (keys are validated, not just present).

- **Ollama** (recommended): `brew install ollama && ollama serve`, then `ollama pull qwen2.5-coder:7b`.
  Hermes caps local context (`HERMES_LOCAL_NUM_CTX`, default 8192) and sends local models a
  pruned 16-tool set so 8B models stay fast.
- **FreeLLMAPI** (your gateway to 67 free models): launch it, set `FREELLMAPI_API_KEY`
  (and `FREELLMAPI_HOST` if not localhost:3002). Hermes auto-detects when it's up.
- **Free cloud tiers** (optional): Groq (`GROQ_API_KEY`), Google AI Studio (`GOOGLE_API_KEY`),
  Mistral, Cerebras via OpenRouter — all have free tiers. Put keys in `.env`.

Useful env vars: `HERMES_AUTO_ROUTE=off` (disable routing), `HERMES_LLM_TIMEOUT=120`,
`OLLAMA_MODEL=<name>` (default local model), `HERMES_SUBCONSCIOUS=off`.

## First launch

Hermes interviews you (role, domains, goals) and pre-builds skills for your persona,
scans the machine for missing dependencies, and shows which models your hardware can run.
Skippable; rerun with `/profile rebuild`.

## Everyday commands

`/memory`, `/remember`, `/dream`, `/blast <file>`, `/experts <q>`, `/max <q>`, `/route <q>`,
`/doctor`, `/models`, `/mcp list`, `/calibration` — full list via `/help` in the TUI.
