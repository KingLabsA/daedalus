# Hermes Ultimate

A production-grade autonomous coding agent with 20+ providers, self-learning, kanban orchestration, plugin marketplace, Docker sandboxing, safety modes, codebase indexing, git checkpoints, lifecycle hooks, and a standalone Tauri 2 desktop app.

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
| `diff` | Get git diff |
| `sessions` | List sessions |
| `approve:<id>` | Approve pending action |
| `deny:<id>` | Deny pending action |

## Project Structure

```
hermes-ultimate/
├── agent_ultimate.py     # ~1800 lines — all phases, providers, WS, plugins, safety, indexing
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

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=agent_ultimate

# Frontend type check
cd desktop && npx tsc --noEmit

# Frontend build
cd desktop && npm run build
```

## License

MIT
