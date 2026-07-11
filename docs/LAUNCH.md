# Launch posts

Drafts for r/LocalLLaMA and Show HN. Every claim below is verified in the repo —
no benchmark numbers are quoted because SWE-bench hasn't been run yet (the harness
exists at `bench/swebench_runner.py`; run it before claiming a score).

---

## r/LocalLLaMA

**Title:** Daedalus — a local-first coding agent with persistent memory, a failure "immune system", and sleep-time compute. Runs entirely on Ollama. Terminal + Web IDE + VS Code.

I got tired of coding assistants that (a) only think when you prompt them, (b) forget everything between sessions, and (c) phone home to a cloud model. So I built **Daedalus** — one agent engine, local-first, that runs on your own Ollama models and only reaches for a cloud provider if you give it a key.

`pip install daedalus-ai` → `daedalus` (terminal), `daedalus web` (browser IDE), or the VS Code extension.

**What's actually different from Cursor / Continue / Cline:**

- **Persistent memory across sessions** (SQLite + FTS) — it remembers your project, decisions, and preferences, and rebuilds context from structured checkpoints instead of just truncating.
- **Failure Immune System** — every tool failure becomes a searchable "antibody"; before repeating an action it checks whether it got burned this way before, in *this* repo.
- **Subconscious (sleep-time compute)** — while idle it consolidates session experience into memory and distills repeated workflows into reusable skills. It keeps improving when you're not typing.
- **Judge-verified goals** — an independent model has to confirm a goal is truly done before it stops (kills the "optimistic early stop" every agent has).
- **Causal World Model** — predicts the blast radius of an edit from your git co-change history *before* it makes the change.
- **MoE routing over 23 providers** — classifies each task and routes it to the best *validated-live* provider (it pings them; no dead-key false positives). Easy work → free local model, hard work → the strongest one you've configured.
- **Learned calibration** — it records predicted confidence vs actual outcomes, so "90%" starts meaning 90% *in your environment*.

**Local-model reality check I hit and fixed:** Ollama's OpenAI-compatible endpoint silently ignores `num_ctx`, so an 8B model with a 131k Modelfile context loads at ~23 GB and swaps your machine to death. Daedalus routes local models through the native `/api/chat` and caps context — hermes3:8b loads at 8k/5.7 GB and answers in seconds.

**Bonus (text-to-app):** it scaffolds 17 kinds of runnable projects (Vite/Tailwind/Next/T3/Supabase/Astro/Svelte/Expo/FastAPI/CLI, and even a self-extending MCP tool server), verifies them (build must pass / tests green / MCP must handshake) with an **eval gate that blocks deploy on failure**, then prepares a Vercel/Netlify/Fly/EAS deploy. All one-click in the web IDE.

Free, MIT, ~280 tests. Links: PyPI `daedalus-ai` · GitHub KingLabsA/daedalus · VS Code Marketplace "Daedalus".

Would love feedback on the memory/immune-system design especially — it's the part I think is genuinely new.

---

## Show HN

**Title:** Show HN: Daedalus – Local-first coding agent with memory, an immune system, and a deploy gate

Daedalus is a coding agent that runs on your own machine (Ollama local models; cloud optional). One engine, three surfaces: a rich terminal TUI, a self-served web IDE, and a VS Code extension — all off the same WebSocket agent.

The design bets that are different from the usual wrapper-around-an-API:

1. **It has state.** Persistent memory across sessions, structured checkpoints it reconstructs context from, and a "failure immune system" that stops it repeating mistakes it's made in your repo.
2. **It thinks off-clock.** A background "subconscious" consolidates memory and distills repeated tool sequences into skills while you're idle.
3. **It doubts itself, calibrated.** Goals are judge-verified before it stops; it tracks predicted-vs-actual confidence and routes work across 23 providers by learned calibration + validated liveness.
4. **It ships safely.** Text-to-app scaffolds 17 stacks (incl. a self-extending MCP server), an eval gate blocks deploy unless build/tests/handshake pass, then it prepares the deploy.

Honest status: single-user, local-first by design (not multi-tenant/SaaS). Desktop `.app` is parked on an upstream Tauri/macOS bug — the browser IDE is the desktop experience for now. SWE-bench harness is in the repo but I haven't published a score yet, so I'm not quoting one.

`pip install daedalus-ai`, then `daedalus web`. MIT. Repo: github.com/KingLabsA/daedalus. Happy to go deep on the memory + immune-system internals.
