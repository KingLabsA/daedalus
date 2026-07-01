# Hermes Ultimate

A production-grade autonomous coding agent with 20+ providers, self-learning, kanban orchestration, plugin marketplace, Docker sandboxing, and a standalone Tauri 2 desktop app.

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

### Desktop Features Live
- **Chat** — full message log with user/assistant bubbles, tool call badges
- **Kanban** — syncs board state from agent (5s polling via WS)
- **Agent Dashboard** — live stats: provider, tools, skills, tasks, workers
- **Composer** — three modes (Chat / Goal / Multi-task) with clickable tool chips
- **Settings** — 16 providers, model input, feature list, connection indicator

## Features

- **Agent Loop** — Think → Act → Observe, with self-healing retries
- **Self-Learning** — Record a demonstration once → agent generates a reusable skill
- **Self-Verification** — Runs pytest on generated code before accepting it
- **Self-Correction** — Tool errors feed back to the LLM for automatic fix
- **Self-Implementation** — Agent can write, test, and register its own tools
- **Kanban System** — Multi-agent orchestration with heartbeat/zombie detection
- **Advanced Browser** — Playwright-based navigation, clicking, typing, screenshots
- **Desktop Control** — PyAutoGUI for mouse, keyboard, and app launching
- **20+ Providers** — OpenAI, Anthropic, OpenRouter, Ollama, DeepSeek, Zhipu, etc.
- **Persistent Memory** — SQLite sessions survive restarts
- **Context Compression** — Auto-summarizes when history gets long
- **Parallel Execution** — Runs independent tool calls concurrently
- **Sub-Agent Spawning** — Deeply nested agent trees
- **Dynamic Workflows** — /goal commands lock onto objectives
- **Cross-Session Skills** — Saved skills auto-load on startup
- **Plugin Marketplace** — Discover local + remote plugins, versioned skill tracking
- **Docker Sandbox** — `run_command` defaults to Docker containers, falls back to bare shell

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
| `/reset` | Clear conversation |

## WebSocket Protocol

The agent exposes a JSON WebSocket interface on `ws://127.0.0.1:8765`:

| Message Type | Direction | Purpose |
|---|---|---|
| `{"type":"chat","text":"..."}` | Client → Agent | Send a message |
| `{"type":"response","content":"..."}` | Agent → Client | Response |
| `{"type":"command","command":"kanban"}` | Client → Agent | Request state |
| `{"type":"kanban","data":{...}}` | Agent → Client | Board state |
| `{"type":"tools","data":["read_file",...]}` | Agent → Client | Tool list |
| `{"type":"skills","data":["skill1",...]}` | Agent → Client | Learned skills |
| `{"type":"provider","data":"openai"}` | Agent → Client | Active provider |
| `{"type":"plugins","data":[...]}` | Agent → Client | Installed plugins |
| `{"type":"notification","content":"..."}` | Agent → Client | One-shot message |

## Project Structure

```
hermes-ultimate/
├── agent_ultimate.py     # ~700 lines — all phases, providers, WS, plugins
├── requirements.txt       # Python dependencies
├── .env.example           # API key template (16 providers)
├── core/                  # Modular re-exports
├── desktop/               # Tauri 2 standalone app
│   ├── src-tauri/         # Rust backend (spawns agent WS, start/stop)
│   └── src/               # React frontend (5 tabs, Zustand, WS hooks)
├── .hermes/
│   ├── skills/            # Auto-generated skills (self-learning)
│   └── checkpoints/       # State snapshots
└── plugins/               # Plugin marketplace install targets
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
│  Browser UI     │   │  External Clients         │
│  (React/Zustand)│   │  (other apps, scripts)    │
└────────────────┘   └───────────────────────────┘
```
