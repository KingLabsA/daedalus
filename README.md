# Daedalus

**The self-evolving coding assistant, powered by the Hermes Deep Mind engine.**

Daedalus is a multimodal coding assistant built on Hermes — the autonomous agent engine at its core. 83 tools, 22 providers, and a cognitive stack no other assistant ships:

- **Persistent memory + context reconstruction** — remembers across sessions; rebuilds context from structured checkpoints instead of truncating ([spec](docs/superpowers/specs/2026-07-02-hermes-deep-mind-design.md))
- **Failure Immune System** — every failure becomes an antibody; it cannot repeat a mistake it has already made
- **Subconscious (sleep-time compute)** — dreams session experience into memory and distills repeated workflows into skills while idle
- **Judge-verified goals** — an independent model must confirm a `/goal` is truly complete
- **Causal World Model** — predicts the blast radius of an edit from git co-change history before making it
- **Model Orchestra (agent-level MoE)** — classifies each task and routes it to the best expert among 22 providers; committees + judged Max Mode best-of-N
- **Epistemic Engine** — records predicted confidence vs actual outcomes; cost-aware auto-routing driven by *learned* calibration (easy → free local model, hard → Claude Fable 5)
- **Senses** — image analysis, video understanding (ffmpeg frame sampling), voice in (`/listen`) and out (`/say`)
- **Native MCP client** — connect any Model Context Protocol server via `.hermes/mcp.json`
- **Device doctor + model advisor** — scans the machine for missing dependencies; recommends exactly which models this hardware can run
- **Profile builder** — first launch interviews you (developer, PM, doctor, engineer, …) and pre-builds persona skill packs

Plus the original core: self-learning, kanban multi-agent orchestration, plugin marketplace, Docker sandboxing, safety modes, git checkpoints, lifecycle hooks, and a desktop app (React + Tauri 2) with a **Mind** dashboard.

**Install:** `pip install daedalus-ai` → run `daedalus` (terminal UI), `daedalus web` (browser IDE), or the [VS Code extension](vscode-extension/). [PyPI](https://pypi.org/project/daedalus-ai/)

See [CHANGELOG.md](CHANGELOG.md) for the full six-phase build.

## Quick Start

```bash
# 1. Install Python deps
pip install -r requirements.txt

# 2. Configure provider
cp .env.example .env
# Edit .env with your API keys

# 3. Run CLI mode
python agent_ultimate.py

# 4. Or run WebSocket server (for desktop app)
python agent_ultimate.py ws
```

## Desktop App

```bash
cd desktop
npm install
npm run tauri dev
```

The desktop app spawns the agent in WebSocket mode automatically. The React frontend connects to `ws://127.0.0.1:8765` to communicate with the agent.

> **Known Issue:** Tauri 2.11.x (tao 0.35.3) panics on macOS 26 Tahoe — upstream bug [tauri-apps/tao#1171](https://github.com/tauri-apps/tao/issues/1171). Use the browser fallback until fixed.

### Browser Fallback (Recommended)

The frontend works standalone in a browser — no Tauri needed:

```bash
# Terminal 1: Start the agent
python agent_ultimate.py ws

# Terminal 2: Start the frontend dev server
cd desktop && npm run dev
# Open http://localhost:5173
```


### Desktop Features
- **Chat** — Full message log with user/assistant bubbles, tool call badges, Cmd+Enter to send
- **Kanban** — Syncs board state from agent with todo/wip/review/done columns
- **Agent Dashboard** — Live stats: provider, tools, skills, tasks, cost, safety mode, checkpoints
- **Composer** — Three modes (Chat / Goal / Multi-task) with inline model selector and safety badge
- **Settings** — 5 tabs: General / Safety / Checkpoints / Index / Hooks
  - **General** — Provider selection, model switching, test connection, system prompt, cost tracking
  - **Safety** — Mode selector (suggest/plan/auto), pending approval management
  - **Checkpoints** — Create/rollback git stash-based checkpoints
  - **Index** — Codebase indexing stats, keyword search
  - **Hooks** — Lifecycle hook event log (last 100 events)

## Features

### Core Agent
- **Agent Loop** — Think → Act → Observe, with self-healing retries
- **Self-Learning** — Record a demonstration once → agent generates a reusable skill
- **Self-Verification** — Runs pytest on generated code before accepting it
- **Self-Correction** — Tool errors feed back to the LLM for automatic fix
- **Self-Implementation** — Agent can write, test, and register its own tools

### Safety & Control
- **Safety Modes** — Suggest (approve each action), Plan (approve before execution), Auto (autonomous with rails)
- **Pending Approvals** — Review and approve/deny destructive tool calls
- **Prompt Injection Detection** — Blocks suspicious input patterns
- **Git Checkpoints** — Save/restore state with git stash-based snapshots

### Codebase Intelligence
- **Codebase Indexing** — Keyword-based semantic search across project files
- **File Hashing** — Tracks changed files for incremental reindexing
- **Search** — Find functions, classes, patterns across the codebase

### Orchestration
- **Kanban System** — Multi-agent orchestration with heartbeat/zombie detection
- **Parallel Execution** — Runs independent tool calls concurrently
- **Sub-Agent Spawning** — Deeply nested agent trees
- **Dynamic Workflows** — /goal commands lock onto objectives
- **Lifecycle Hooks** — pre_tool, post_tool, pre_llm, post_llm, on_error, on_start, on_stop, pre_commit, post_commit

### Providers & Integration
- **20+ Providers** — OpenAI, Anthropic, OpenRouter, Ollama, DeepSeek, Zhipu, Google Gemini, Groq, Mistral, Cohere, Together, Fireworks, Perplexity, Novita, xAI, Moonshot
- **Model Switching** — Change models at runtime without restart
- **Cost Tracking** — Per-session and per-provider token usage and cost
- **Streaming** — Real-time token streaming for all providers (OpenAI, Anthropic, Google native; others via OpenAI-compatible fallback)

### Infrastructure
- **Persistent Memory** — SQLite sessions survive restarts
- **Context Compression** — Auto-summarizes when history gets long
- **Plugin Marketplace** — Discover local + remote plugins, versioned skill tracking
- **Docker Sandbox** — `run_command` defaults to Docker containers, falls back to bare shell
- **Advanced Browser** — Playwright-based navigation, clicking, typing, screenshots
- **Desktop Control** — PyAutoGUI for mouse, keyboard, and app launching

## Commands (CLI & Desktop)

| Command | Description |
|---------|-------------|
| `/goal <objective>` | Set and pursue a high-level goal |
| `/multitask task1 \| task2` | Run parallel sub-agents |
| `/kanban add <title>` | Add task to kanban board |
| `/kanban show` | Display board state |
| `/browser goto <url>` | Navigate in browser |
| `/browser screenshot` | Capture page screenshot |
| `/desktop open <app>` | Launch a desktop app |
| `/record <name> <desc>` | Start skill recording |
| `/stop_record` | Generate skill from demo |
| `/provider <name>` | Switch LLM provider at runtime |
| `/compose skill1,skill2 <goal>` | Chain skills into workflow |
| `/checkpoint create [label]` | Create a git checkpoint |
| `/checkpoint list` | List all checkpoints |
| `/checkpoint restore <label>` | Restore a checkpoint |
| `/index` | Index the codebase |
| `/search <query>` | Search the codebase index |
| `/safety [mode]` | Get/set safety mode (suggest/plan/auto) |
| `/reset` | Clear conversation |
| `/memory [query]` | Memory stats or search persistent memory |
| `/remember <fact>` | Save a fact to persistent memory |
| `/dream` | Consolidate recent sessions into memory |
| `/distill` | Mine repeated workflows into skills |
| `/subconscious` | Sleep-time compute status |
| `/blast <file>` | Predict blast radius of editing a file |
| `/experts [prompt]` | Expert providers / ask a committee |
| `/max <prompt>` | Judged best-of-N across providers |
| `/route <prompt>` | Show cost-aware routing decision |
| `/calibration` | Predicted-vs-actual confidence report |
| `/see <image> [q]` | Analyze an image |
| `/say <text>` / `/listen [s]` | Voice out / voice in |
| `/doctor` | Scan device for missing dependencies |
| `/models` | Models this machine can run |
| `/profile [rebuild]` | Show / rebuild your persona profile |
| `/mcp list\|tools\|call` | MCP servers, tools, invocation |
| `@path/to/file` | Attach a file's contents into your message |
| `Ctrl-C` (TUI) / Stop (web) | Cancel the current run mid-stream |

## WebSocket Protocol

The agent exposes a JSON WebSocket interface on `ws://127.0.0.1:8765`:

| Message Type | Direction | Purpose |
|---|---|---|
| `{"type":"chat","text":"..."}` | Client → Agent | Send a message |
| `{"type":"response","content":"..."}` | Agent → Client | Response |
| `{"type":"token","content":"..."}` | Agent → Client | Streaming token |
| `{"type":"command","command":"..."}` | Client → Agent | Request state |
| `{"type":"kanban","data":{...}}` | Agent → Client | Board state |
| `{"type":"tools","data":["read_file",...]}` | Agent → Client | Tool list |
| `{"type":"skills","data":["skill1",...]}` | Agent → Client | Learned skills |
| `{"type":"provider","data":"openai"}` | Agent → Client | Active provider |
| `{"type":"model","data":"gpt-4o"}` | Agent → Client | Active model |
| `{"type":"safety_mode","data":"auto"}` | Agent → Client | Safety mode |
| `{"type":"checkpoints","data":[...]}` | Agent → Client | Checkpoint list |
| `{"type":"index_stats","data":{...}}` | Agent → Client | Index statistics |
| `{"type":"pending_approvals","data":[...]}` | Agent → Client | Pending approvals |
| `{"type":"cost","data":{...}}` | Agent → Client | Cost summary |
| `{"type":"plugins","data":[...]}` | Agent → Client | Installed plugins |
| `{"type":"notification","content":"..."}` | Agent → Client | One-shot message |

### WS Commands

| Command | Description |
|---------|-------------|
| `tools` | List registered tools |
| `skills` | List learned skills |
| `kanban` | Get board state |
| `kanban:add:<title>` | Add kanban task |
| `kanban:move:<id>:<col>` | Move task to column |
| `kanban:remove:<id>` | Remove task |
| `provider:<name>` | Switch provider |
| `models` | List available models for current provider |
| `model:<name>` | Switch model |
| `safety:mode:<mode>` | Set safety mode |
| `safety:status` | Get safety mode |
| `safety:pending` | Get pending approvals |
| `checkpoints` | List checkpoints |
| `checkpoint:create:<label>` | Create checkpoint |
| `checkpoint:restore:<label>` | Restore checkpoint |
| `index` | Index codebase |
| `index:stats` | Get index stats |
| `index:search:<query>` | Search index |
| `cost` | Get cost summary |
| `logs` | Get agent logs |
| `watcher:start` | Start file watcher |
| `watcher:stop` | Stop file watcher |
| `watcher:status` | Get watcher status |
| `diff` | Get git diff |
| `sessions` | List sessions |
| `approve:<id>` | Approve pending action |
| `deny:<id>` | Deny pending action |

## Project Structure

```
hermes-ultimate/
├── agent_ultimate.py     # ~2200 lines — all phases, providers, WS, plugins, safety, indexing
├── requirements.txt       # Python dependencies
├── .env.example           # API key template (20 providers)
├── core/                  # Modular re-exports
│   ├── __init__.py        # Re-exports from agent_ultimate
│   ├── agent.py           # UltimateAgent wrapper
│   ├── memory.py          # SessionStore, compress_messages
│   ├── providers.py       # ProviderRouter
│   ├── tools.py           # SelfLearner, SelfHealer, etc.
│   ├── kanban.py          # KanbanBoard, GoalManager, ParallelExecutor
│   └── checkpoint/        # CheckpointManager
├── desktop/               # Tauri 2 standalone app
│   ├── src-tauri/         # Rust backend (spawns agent WS, start/stop)
│   └── src/               # React frontend (5 tabs, Zustand, WS hooks)
│       ├── components/
│       │   ├── AgentView/   # AgentDashboard with real-time stats
│       │   ├── Chat/        # ChatView with streaming
│       │   ├── Composer/    # Model selector, safety badge, modes
│       │   ├── Settings/    # 5-tab settings panel
│       │   ├── Git/         # GitPanel
│       │   ├── Kanban/      # KanbanBoard
│       │   └── Files/       # FileExplorer
│       ├── hooks/           # useWebSocket, useAgent
│       └── store/           # Zustand session store
├── tests/
│   └── test_core.py        # 40 unit tests
├── .hermes/
│   ├── skills/             # Auto-generated skills (self-learning)
│   └── checkpoints/        # State snapshots
└── plugins/                # Plugin marketplace install targets
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Tauri 2 Desktop (Rust)                 │
│  ┌──────────────┐  ┌─────────────────────────────────┐  │
│  │  Process Mgmt │  │  Tauri Commands (IPC)           │  │
│  │  start_agent  │  │  start_agent / stop_agent /     │  │
│  │  stop_agent   │  │  agent_status                   │  │
│  └──────┬───────┘  └─────────────────────────────────┘  │
│         │ spawns                                        │
│         ▼                                                │
│  ┌──────────────────┐   WebSocket (ws://127.0.0.1:8765) │
│  │  Python Agent     │◄──────────────────────────────────│
│  │  (agent_ultimate) │                                   │
│  │  ─ ws mode ────── │                                   │
│  └──────────────────┘                                   │
└─────────────────────────────────────────────────────────┘
         ▲                        ▲
         │  WebSocket             │  WebSocket
         ▼                        ▼
┌────────────────┐   ┌───────────────────────────┐
│  Desktop UI     │   │  External Clients         │
│  (React/Zustand)│   │  (other apps, scripts)    │
└────────────────┘   └───────────────────────────┘
```

## Hermes Models

Hermes Ultimate ships with Nous Hermes 3 as the default provider. Available sizes:

| Model | Size | Context | Best For |
|-------|------|---------|----------|
| `hermes3:3b` | 1.7 GB | 131K | Fast tasks, lightweight coding |
| `hermes3:8b` | 4.7 GB | 131K | General coding (default) |
| `hermes3:70b` | 40 GB | 131K | Complex reasoning, architecture |
| `hermes3:405b` | 231 GB | 131K | Maximum capability |

All Hermes models support tool use and reasoning. The agent automatically routes through Ollama (`http://localhost:11434`).

## Testing

```bash
# Run all tests
python3 tests/test_e2e_ws.py  # 44/44 E2E tests
python3 -m pytest tests/ -v  # 58 unit tests

# Run with coverage
python3 tests/test_e2e_ws.py  # 44/44 E2E tests
python3 -m pytest tests/ -v  # 58 unit tests --cov=agent_ultimate

# Frontend type check
cd desktop && npx tsc --noEmit

# Frontend build
cd desktop && npm run build
```

## License

MIT
