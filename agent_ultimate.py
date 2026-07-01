#!/usr/bin/env python3
import os, sys, json, sqlite3, subprocess, threading, time, asyncio, tempfile, inspect, importlib, io, uuid, socket
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
MAX_MESSAGES = 30
DB_FILE = os.getenv("DB_FILE", "hermes_ultimate.db")
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", ".hermes/skills"))
SKILLS_DIR.mkdir(parents=True, exist_ok=True)
WS_HOST = os.getenv("WS_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
PLUGINS_DIR = Path(os.getenv("PLUGINS_DIR", "plugins"))
PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
PLUGIN_REGISTRY_URL = os.getenv("PLUGIN_REGISTRY_URL", "https://hermes-plugins.fake")

# Streaming queue for live command output
_stream_queue: List[Dict] = []
_stream_lock = threading.Lock()
def _push_stream(entry: Dict):
    with _stream_lock:
        _stream_queue.append(entry)
def _drain_stream() -> List[Dict]:
    with _stream_lock:
        out = list(_stream_queue)
        _stream_queue.clear()
        return out

# Cost tracking
COST_PER_1K: Dict[str, Dict[str, float]] = {
    "openai": {"input": 0.00015, "output": 0.0006},
    "anthropic": {"input": 0.003, "output": 0.015},
    "groq": {"input": 0.0001, "output": 0.0001},
    "mistral": {"input": 0.0001, "output": 0.0003},
    "google": {"input": 0.0001, "output": 0.0004},
    "deepseek": {"input": 0.00014, "output": 0.00028},
    "together": {"input": 0.0001, "output": 0.0001},
    "fireworks": {"input": 0.0001, "output": 0.0001},
    "xai": {"input": 0.00015, "output": 0.0006},
    "perplexity": {"input": 0.0001, "output": 0.0001},
    "novita": {"input": 0.0001, "output": 0.0002},
    "openrouter": {"input": 0.0001, "output": 0.0001},
    "zhipu": {"input": 0.0001, "output": 0.0001},
    "moonshot": {"input": 0.0001, "output": 0.0001},
    "cohere": {"input": 0.00015, "output": 0.0006},
    "ollama": {"input": 0, "output": 0},
}

session_costs: List[Dict] = []

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._metadata: Dict[str, Dict] = {}
    def register(self, name: str = None, description: str = ""):
        def decorator(func):
            tool_name = name or func.__name__
            self._tools[tool_name] = func
            self._metadata[tool_name] = {"description": description or func.__doc__ or "", "signature": inspect.signature(func)}
            return func
        return decorator
    def get(self, name: str) -> Optional[Callable]:
        return self._tools.get(name)
    def list_tools(self) -> List[str]:
        return list(self._tools.keys())
    def execute(self, name: str, args: Dict[str, Any]) -> str:
        func = self.get(name)
        if not func: return f"Error: Tool '{name}' not found."
        try: return str(func(**args))
        except Exception as e: return f"ToolError: {e}"
    def execute_parallel(self, calls: List[Dict]) -> List[Dict]:
        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = {executor.submit(self.execute, c["name"], c["args"]): c for c in calls}
            results = []
            for future in as_completed(futures):
                call = futures[future]; results.append({"id": call["id"], "name": call["name"], "result": future.result()})
            return results
    def get_openai_schemas(self):
        schemas = []
        for name, func in self._tools.items():
            sig = inspect.signature(func)
            params = {"type": "object", "properties": {}, "required": []}
            for p_name, p_param in sig.parameters.items():
                params["properties"][p_name] = {"type": "string"}
                if p_param.default == inspect.Parameter.empty: params["required"].append(p_name)
            schemas.append({"type": "function", "function": {"name": name, "description": self._metadata[name]["description"], "parameters": params}})
        return schemas
    def get_anthropic_schemas(self):
        schemas = []
        for name, func in self._tools.items():
            sig = inspect.signature(func)
            params = {"type": "object", "properties": {}, "required": []}
            for p_name, p_param in sig.parameters.items():
                params["properties"][p_name] = {"type": "string"}
                if p_param.default == inspect.Parameter.empty: params["required"].append(p_name)
            schemas.append({"name": name, "description": self._metadata[name]["description"], "input_schema": params})
        return schemas

registry = ToolRegistry()

@registry.register(description="Read the contents of a file")
def read_file(filepath: str) -> str:
    with open(os.path.expanduser(filepath), "r") as f: return f.read()

def _git_stage(path: str) -> str:
    try:
        r = subprocess.run(["git", "add", path], capture_output=True, text=True)
        return r.stderr or ""
    except: return ""

def _git_diff(path: str = "") -> str:
    try:
        cmd = ["git", "diff", "--no-color", path] if path else ["git", "diff", "--no-color"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.stdout or "(no diff)"
    except: return ""

def _pyright_diagnostics(target: str = ".") -> str:
    try:
        r = subprocess.run(["pyright", target], capture_output=True, text=True, timeout=30)
        return r.stdout or "(no output)"
    except FileNotFoundError: return "pyright not installed. Run: npm install -g pyright"
    except Exception as e: return f"pyright error: {e}"

@registry.register(description="Write content to a file (auto-stages changes for git)")
def write_file(filepath: str, content: str) -> str:
    fp = os.path.expanduser(filepath)
    before = ""
    if os.path.exists(fp):
        with open(fp) as f: before = f.read()
    with open(fp, "w") as f: f.write(content)
    diff = ""
    if before:
        diff = _git_diff(filepath) if before == "" else _git_diff(filepath)
    _git_stage(fp)
    _push_stream({"type":"file_written","filepath":filepath})
    return f"Written to {filepath}"

@registry.register(description="Append content to a file (auto-stages changes)")
def append_file(filepath: str, content: str) -> str:
    fp = os.path.expanduser(filepath)
    before = ""
    if os.path.exists(fp):
        with open(fp) as f: before = f.read()
    with open(fp, "a") as f: f.write(content)
    _git_stage(fp)
    _push_stream({"type":"file_written","filepath":filepath})
    return f"Appended to {filepath}"

@registry.register(description="Edit file by replacing exact string match. Like sed but safe. Staged for git.")
def edit_file_line(filepath: str, old_string: str, new_string: str) -> str:
    fp = os.path.expanduser(filepath)
    if not os.path.exists(fp): return f"Error: file not found: {fp}"
    with open(fp) as f: content = f.read()
    if old_string not in content: return f"Error: old_string not found in {filepath}"
    new_content = content.replace(old_string, new_string, 1)
    with open(fp, "w") as f: f.write(new_content)
    _git_stage(fp)
    _push_stream({"type":"file_edited","filepath":filepath,"old":old_string[:40],"new":new_string[:40]})
    return f"Edited {filepath}"

@registry.register(description="Preview uncommitted git diff (unified format). Best called BEFORE write_file to show what will change.")
def git_diff_preview(path: str = "") -> str:
    return _git_diff(path)

@registry.register(description="Show git status — tracked/untracked/modified files")
def git_status() -> str:
    try:
        r = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
        return r.stdout or "(clean)"
    except Exception as e: return f"git status error: {e}"

@registry.register(description="Stage all changes and commit with a message. If empty, auto-generates from diff.")
def git_commit(message: str = "") -> str:
    try:
        r = subprocess.run(["git", "add", "-A"], capture_output=True, text=True)
        if r.returncode != 0: return f"git add failed: {r.stderr}"
        if not message:
            diff = _git_diff()
            if diff == "(no diff)": return "Nothing to commit."
            lines = diff.split("\n")[:5]
            message = f"auto: {lines[0] if lines else 'update'}"
        r2 = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
        return r2.stdout or r2.stderr or "Committed."
    except Exception as e: return f"git commit error: {e}"

@registry.register(description="Undo last changes: revert uncommitted changes in working tree.")
def git_undo() -> str:
    try:
        r = subprocess.run(["git", "checkout", "--", "."], capture_output=True, text=True)
        return r.stdout or r.stderr or "Undone: working tree clean."
    except Exception as e: return f"git undo error: {e}"

@registry.register(description="Run LSP diagnostics (Python: pyright) on a file or project. Returns errors/warnings.")
def lsp_diagnostics(target: str = ".") -> str:
    return _pyright_diagnostics(target)

@registry.register(description="Stream command output line by line (live). Use for long-running cmds like build/test.")
def run_command(command: str, use_docker: bool = False, image: str = "python:3.12-slim") -> str:
    def _stream_cmd(cmd):
        try:
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in iter(proc.stdout.readline, ""):
                _push_stream({"type":"stream","line":line.rstrip()})
            proc.wait()
            _push_stream({"type":"stream","line":f"[exit code {proc.returncode}]"})
            return f"Exit code: {proc.returncode}"
        except Exception as e:
            _push_stream({"type":"stream","line":f"[error: {e}]"})
            return f"Error: {e}"
    if use_docker and shutil.which("docker"):
        cname = f"hermes-{uuid.uuid4().hex[:8]}"
        mount = f"{os.getcwd()}:/workspace"
        cmd = f'docker run --rm --name {cname} -v "{mount}" -w /workspace {image} sh -c "{command}"'
    else:
        cmd = command
    t = threading.Thread(target=_stream_cmd, args=(cmd,), daemon=True)
    t.start()
    t.join(timeout=120)
    return f"Command started: {command[:80]}"

@registry.register(description="Search for a pattern in files")
def search_code(pattern: str, path: str = ".") -> str:
    result = subprocess.run(f'grep -r -l "{pattern}" {path} 2>/dev/null || echo "No matches."', shell=True, capture_output=True, text=True)
    return result.stdout or "No matches."

@registry.register(description="Execute Python code in a sandbox")
def execute_python(code: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code); f.flush()
        try:
            result = subprocess.run(["python", f.name], capture_output=True, text=True, timeout=10)
            return (result.stdout + result.stderr)
        finally: os.unlink(f.name)

@registry.register(description="Execute a command inside a Docker container")
def docker_execute(image: str = "python:3.12-slim", command: str = "python3 --version", workdir: str = "/workspace") -> str:
    cname = f"hermes-{uuid.uuid4().hex[:8]}"
    mount = f"{os.getcwd()}:/workspace"
    cmd = f'docker run --rm --name {cname} -v "{mount}" -w {workdir} {image} sh -c "{command}"'
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        return (result.stdout + result.stderr) or "(no output)"
    except subprocess.TimeoutExpired: return "Docker command timed out."
    except Exception as e: return f"Docker error: {e}"

@registry.register(description="Fetch a web page")
def web_fetch(url: str) -> str:
    try:
        import requests
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Hermes-Ultimate/1.0"})
        return resp.text[:8000]
    except Exception as e: return f"Fetch error: {e}"

@registry.register(description="Search the web using DuckDuckGo")
def web_search(query: str) -> str:
    try:
        import requests
        resp = requests.get(f"https://api.duckduckgo.com/?q={query}&format=json", timeout=10)
        data = resp.json()
        results = data.get("RelatedTopics", [])[:5]
        return json.dumps([r.get("Text", r.get("Result", "")) for r in results], indent=2)
    except Exception as e: return f"Search error: {e}"

@registry.register(description="Get current timestamp")
def get_time() -> str:
    return datetime.now().isoformat()

@registry.register(description="Map the repository structure — returns file tree and language stats.")
def map_repo(path: str = ".") -> str:
    try:
        root = Path(os.path.expanduser(path)).resolve()
        tree = []
        lang = {}
        for f in sorted(root.rglob("*"), key=lambda x: (len(x.parents), x.name)):
            if ".git" in f.parts or "__pycache__" in f.parts or f.name.startswith("."):
                continue
            if f.is_file():
                indent = "  " * (len(f.relative_to(root).parents) - 1)
                tree.append(f"{indent}{f.name}")
                ext = f.suffix.lower()
                lang[ext] = lang.get(ext, 0) + 1
        langs = ", ".join(sorted(f"{k} ({v})" for k, v in lang.items() if v > 1))
        return f"**{root.name}** ({len(tree)} files)\nLangs: {langs}\n" + "\n".join(tree[:200])
    except Exception as e: return f"map error: {e}"

@registry.register(description="Process an image file — returns base64 data for LLM vision.")
def process_image(filepath: str) -> str:
    try:
        fp = os.path.expanduser(filepath)
        if not os.path.exists(fp): return f"Error: file not found: {fp}"
        import base64
        with open(fp, "rb") as f: b64 = base64.b64encode(f.read()).decode()
        ext = Path(fp).suffix.lower()
        return f"data:image/{ext[1:] if ext else 'png'};base64,{b64[:50000]}"
    except Exception as e: return f"process_image error: {e}"

@registry.register(description="Call an MCP server tool. Pass server name, tool name, and arguments as JSON string.")
def mcp_call(server: str, tool: str, arguments: str = "{}") -> str:
    try:
        import subprocess, json
        args = json.loads(arguments)
        # assume mcp CLI tool is installed
        cmd = ["mcp", "call", server, tool] if not args else ["mcp", "call", server, tool, "-a", json.dumps(args)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout or r.stderr or "(no output)"
    except FileNotFoundError:
        return "MCP CLI not installed. Install: pip install mcp"
    except Exception as e:
        return f"mcp call error: {e}"

@registry.register(description="Test a provider connection by sending a tiny prompt. Returns latency and model info.")
def test_provider(provider: str = "") -> str:
    target = provider or LLM_PROVIDER
    try:
        cfg = PROVIDER_CONFIGS.get(target)
        if not cfg: return f"Unknown provider: {target}"
        api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
        if cfg.get("env") and not api_key:
            return f"{target}: NO API KEY (set {cfg['env']})"
        t0 = time.time()
        client = _get_provider_client(target)
        resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":"say ok"}], max_tokens=5)
        lat = round(time.time() - t0, 2)
        return f"{target}: OK ({lat}s) via {cfg.get('default_model','?')}"
    except Exception as e:
        return f"{target}: FAIL ({e})"

# Config file loader
HERMES_CONFIG_PATH = ".hermes.json"
def _load_config() -> dict:
    path = Path(HERMES_CONFIG_PATH)
    if path.exists():
        return json.loads(path.read_text())
    return {}
def _save_config(cfg: dict):
    Path(HERMES_CONFIG_PATH).write_text(json.dumps(cfg, indent=2))

def _track_cost(provider: str, in_tokens: int, out_tokens: int):
    rates = COST_PER_1K.get(provider, {"input": 0.0005, "output": 0.0015})
    cost = (in_tokens / 1000) * rates["input"] + (out_tokens / 1000) * rates["output"]
    session_costs.append({"provider": provider, "input": in_tokens, "output": out_tokens, "cost": round(cost, 6), "ts": datetime.now().isoformat()})
def _get_cost_summary() -> dict:
    total_cost = sum(c["cost"] for c in session_costs)
    total_in = sum(c["input"] for c in session_costs)
    total_out = sum(c["output"] for c in session_costs)
    by_provider = {}
    for c in session_costs:
        p = c["provider"]
        if p not in by_provider: by_provider[p] = {"calls": 0, "input": 0, "output": 0, "cost": 0}
        by_provider[p]["calls"] += 1
        by_provider[p]["input"] += c["input"]
        by_provider[p]["output"] += c["output"]
        by_provider[p]["cost"] += c["cost"]
    return {"total_cost": round(total_cost, 4), "total_input_tokens": total_in, "total_output_tokens": total_out, "session_calls": len(session_costs), "by_provider": by_provider}

# ============== SELF-LEARNER ==============
class SelfLearner:
    _recording = False; _recording_name = ""; _recording_description = ""; _action_log = []
    @classmethod
    def start_recording(cls, name: str, description: str):
        cls._recording = True; cls._recording_name = name; cls._recording_description = description
        cls._action_log = []; print(f"🔴 Recording: '{name}'")
    @classmethod
    def record_action(cls, action_type: str, details: dict):
        if cls._recording:
            cls._action_log.append({"step": len(cls._action_log)+1, "type": action_type, "details": details, "timestamp": datetime.now().isoformat()})
    @classmethod
    def stop_recording(cls, provider: str = "openai") -> str:
        if not cls._recording: return "Not recording."
        cls._recording = False
        if not cls._action_log: return "No actions recorded."
        prompt = f"""Generate a reusable SKILL from these recorded tool calls.
Description: "{cls._recording_description}"
Actions: {json.dumps(cls._action_log, indent=2)}

Output markdown format:
---
name: {cls._recording_name}
description: <summary>
parameters:
  - name: param1
    type: string
    description: <what this means>
workflow:
  - step: 1
    action: tool_name
    parameters:
      arg: "{{{{param1}}}}"
---
Replace ALL concrete values with {{placeholders}}. Return ONLY markdown."""
        try:
            client = _get_provider_client(provider)
            resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.2)
            skill_md = resp.choices[0].message.content
        except Exception as e:
            skill_md = f"---\nname: {cls._recording_name}\ndescription: {cls._recording_description}\n---\n{json.dumps(cls._action_log, indent=2)}"
        skill_path = SKILLS_DIR / f"{cls._recording_name}.md"
        skill_path.write_text(skill_md); cls._action_log = []
        return f"✅ Skill saved: {skill_path}\n{skill_md}"
    @classmethod
    def compose_workflow(cls, skill_names: List[str], goal: str, provider: str = "openai") -> str:
        """Chain multiple skills into a composed workflow."""
        skill_contents = []
        for name in skill_names:
            path = SKILLS_DIR / f"{name}.md"
            if path.exists(): skill_contents.append(path.read_text())
        if not skill_contents: return "No skills found."
        prompt = f"""You have these skills: {json.dumps(skill_contents, indent=2)}
Goal: {goal}
Create a NEW composed workflow that chains these skills together, reusing their steps. Output SKILL.md format."""
        try:
            client = _get_provider_client(provider)
            resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.3)
            workflow = resp.choices[0].message.content
        except Exception as e: return f"Compose failed: {e}"
        path = SKILLS_DIR / f"composed_{int(time.time())}.md"
        path.write_text(workflow)
        return f"✅ Composed workflow saved: {path}\n{workflow}"
    @staticmethod
    def save_skill(name: str, description: str, workflow: list):
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        (SKILLS_DIR / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{json.dumps(workflow, indent=2)}")
    @staticmethod
    def load_skills() -> list: return [f.stem for f in SKILLS_DIR.glob("*.md")]

# ============== PROVIDERS ==============
PROVIDER_CONFIGS = {
    "openai":     {"env": "OPENAI_API_KEY",       "lib": "openai",     "client": "OpenAI",     "default_model": "gpt-4o-mini"},
    "anthropic":  {"env": "ANTHROPIC_API_KEY",    "lib": "anthropic",  "client": "Anthropic",  "default_model": "claude-3-5-sonnet-20241022"},
    "openrouter": {"env": "OPENROUTER_API_KEY",   "lib": "openai",     "base": "https://openrouter.ai/api/v1", "default_model": "openai/gpt-4o-mini"},
    "ollama":     {"env": "",                     "lib": "openai",     "base": os.getenv("OLLAMA_HOST","http://localhost:11434")+"/v1", "default_model": "qwen2.5-coder:7b"},
    "google":     {"env": "GOOGLE_API_KEY",       "lib": "google.generativeai", "default_model": "gemini-1.5-pro"},
    "groq":       {"env": "GROQ_API_KEY",         "lib": "openai",     "base": "https://api.groq.com/openai/v1", "default_model": "llama3-70b-8192"},
    "xai":        {"env": "XAI_API_KEY",          "lib": "openai",     "base": "https://api.x.ai/v1", "default_model": "grok-2-1212"},
    "deepseek":   {"env": "DEEPSEEK_API_KEY",     "lib": "openai",     "base": "https://api.deepseek.com", "default_model": "deepseek-chat"},
    "zhipu":      {"env": "ZHIPU_API_KEY",        "lib": "openai",     "base": "https://open.bigmodel.cn/api/paas/v4", "default_model": "glm-4-plus"},
    "moonshot":   {"env": "MOONSHOT_API_KEY",     "lib": "openai",     "base": "https://api.moonshot.cn/v1", "default_model": "moonshot-v1-8k"},
    "mistral":    {"env": "MISTRAL_API_KEY",      "lib": "openai",     "base": "https://api.mistral.ai/v1", "default_model": "mistral-large-latest"},
    "together":   {"env": "TOGETHER_API_KEY",     "lib": "openai",     "base": "https://api.together.xyz/v1", "default_model": "mistralai/Mixtral-8x7B-Instruct-v0.1"},
    "fireworks":  {"env": "FIREWORKS_API_KEY",    "lib": "openai",     "base": "https://api.fireworks.ai/inference/v1", "default_model": "accounts/fireworks/models/llama-v3p1-70b-instruct"},
    "cohere":     {"env": "COHERE_API_KEY",       "lib": "cohere",     "default_model": "command-r-plus"},
    "perplexity": {"env": "PERPLEXITY_API_KEY",   "lib": "openai",     "base": "https://api.perplexity.ai", "default_model": "sonar-pro"},
    "novita":     {"env": "NOVITA_API_KEY",       "lib": "openai",     "base": "https://api.novita.ai/v3/openai", "default_model": "meta-llama/llama-3.1-8b-instruct"},
}

def _get_provider_client(provider: str = None):
    provider = provider or LLM_PROVIDER
    cfg = PROVIDER_CONFIGS.get(provider)
    if not cfg: raise ValueError(f"Unknown provider: {provider}")
    lib = cfg.get("lib")
    mod = importlib.import_module(lib)
    client_class_name = cfg.get("client", "OpenAI")
    ClientClass = getattr(mod, client_class_name)
    api_key = os.getenv(cfg["env"]) if cfg.get("env") else "none"
    base = cfg.get("base")
    if base: return ClientClass(api_key=api_key, base_url=base)
    return ClientClass(api_key=api_key) if api_key else ClientClass()

class ProviderRouter:
    @staticmethod
    def call(messages: List[Dict], tools_schemas: List[Dict], provider: str = None):
        provider = provider or LLM_PROVIDER
        cfg = PROVIDER_CONFIGS.get(provider)
        if not cfg: raise ValueError(f"Unsupported provider: {provider}")
        model = os.getenv("MODEL_NAME", cfg.get("default_model", "gpt-4o-mini"))

        base = cfg.get("base")
        api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
        lib = cfg.get("lib")

        if lib == "openai":
            import openai
            client = openai.OpenAI(api_key=api_key, base_url=base) if api_key or base else openai.OpenAI()
            om = []
            for m in messages:
                if m["role"] == "system": om.append({"role": "system", "content": m["content"]})
                elif m["role"] == "user": om.append({"role": "user", "content": m.get("content","")})
                elif m["role"] == "assistant": om.append({"role": "assistant", "content": m.get("content","")})
                elif m["role"] == "tool": om.append({"role": "tool", "tool_call_id": m.get("tool_call_id",""), "content": m["content"]})
            resp = client.chat.completions.create(model=model, messages=om, tools=tools_schemas if tools_schemas else None, tool_choice="auto" if tools_schemas else None)
            _track_cost(provider, resp.usage.prompt_tokens if resp.usage else 0, resp.usage.completion_tokens if resp.usage else 0)
            return resp.choices[0].message

        elif lib == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            cm = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ["user","assistant"]]
            an_tools = [{"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tools_schemas]
            resp = client.messages.create(model=model, system=system, messages=cm, tools=an_tools, max_tokens=4096)
            _track_cost(provider, resp.usage.input_tokens if resp.usage else 0, resp.usage.output_tokens if resp.usage else 0)
            class Dummy:
                def __init__(self, r):
                    self.content = None; self.tool_calls = []
                    for b in r.content:
                        if b.type == "text": self.content = b.text
                        elif b.type == "tool_use": self.tool_calls.append({"id": b.id, "function": {"name": b.name, "arguments": json.dumps(b.input)}})
            return Dummy(resp)

        elif lib == "cohere":
            import cohere
            client = cohere.Client(api_key=api_key)
            # cohere uses a different API — simplified chat
            last = messages[-1]["content"] if messages else "Hello"
            resp = client.chat(model=model, message=last)
            class Dummy:
                def __init__(self, r):
                    self.content = r.text; self.tool_calls = []
            return Dummy(resp)

        elif lib == "google.generativeai":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            gen_model = genai.GenerativeModel(model)
            last = messages[-1]["content"] if messages else "Hello"
            resp = gen_model.generate_content(last)
            class Dummy:
                def __init__(self, r):
                    self.content = r.text; self.tool_calls = []
            return Dummy(resp)

        raise ValueError(f"Provider '{provider}' not fully implemented yet.")

# ============== GOAL MANAGER ==============
class GoalManager:
    def __init__(self, goal: str):
        self.goal = goal; self.history = []; self.completed = False
    def is_complete(self, last_output: str) -> bool:
        if "COMPLETE" in last_output.upper(): self.completed = True
        return self.completed

# ============== SUB-AGENT ==============
class SubAgent:
    def __init__(self, name: str, task: str, parent_context: str = ""):
        self.name = name; self.task = task; self.context = parent_context; self.result = None; self.status = "pending"
    def run(self, agent_core) -> str:
        self.status = "running"
        self.result = agent_core.run_loop([{"role": "system", "content": agent_core.system_prompt}, {"role": "user", "content": f"Sub-task '{self.name}': {self.task}\nContext: {self.context}"}], max_iters=5)
        self.status = "done"; return self.result

# ============== PARALLEL EXECUTOR ==============
class ParallelExecutor:
    @staticmethod
    def run(tasks: List[Dict], agent_core):
        results = {}
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {executor.submit(SubAgent(t["name"], t["prompt"], str(t)).run, agent_core): t["name"] for t in tasks}
            for future in as_completed(futures): results[futures[future]] = future.result()
        return results

# ============== KANBAN ==============
@dataclass
class KanbanTask:
    id: str; title: str; description: str; status: str = "todo"; assigned_to: str = None
    retries: int = 0; max_retries: int = 3
    created_at: str = field(default_factory=lambda: datetime.now().isoformat()); agent_context: str = ""

class KanbanWorker:
    def __init__(self, name: str, worker_type: str):
        self.name = name; self.type = worker_type; self.status = "idle"
        self.current_task: Optional[KanbanTask] = None; self.last_heartbeat = datetime.now()
    def heartbeat(self): self.last_heartbeat = datetime.now()
    def assign(self, task: KanbanTask): self.current_task = task; self.status = "working"

class KanbanBoard:
    def __init__(self):
        self.tasks: List[KanbanTask] = []; self.workers: List[KanbanWorker] = []; self.running = True
        self._guardian = threading.Thread(target=self._guardian_loop, daemon=True); self._guardian.start()
    def add_task(self, title, desc="", context="") -> KanbanTask:
        task = KanbanTask(id=f"t-{len(self.tasks)+1}", title=title, description=desc, agent_context=context)
        self.tasks.append(task); return task
    def add_worker(self, name, worker_type): self.workers.append(KanbanWorker(name, worker_type))
    def assign_work(self):
        for task in self.tasks:
            if task.status == "todo":
                for w in self.workers:
                    if w.status == "idle": w.assign(task); task.status = "in_progress"; task.assigned_to = w.name; break
    def _guardian_loop(self):
        while self.running:
            time.sleep(10)
            now = datetime.now()
            for w in self.workers:
                if (now - w.last_heartbeat).total_seconds() > 30 and w.status == "working":
                    print(f"⚠️ Zombie worker: {w.name}")
                    if w.current_task:
                        t = w.current_task; t.status = "todo"; t.retries += 1
                        if t.retries > t.max_retries: t.status = "done"
                    w.status = "idle"; w.current_task = None
    def move_task(self, task_id: str, to_status: str) -> bool:
        for t in self.tasks:
            if t.id == task_id and to_status in ("todo","in_progress","review","done"):
                t.status = to_status; return True
        return False
    def remove_task(self, task_id: str) -> bool:
        for i, t in enumerate(self.tasks):
            if t.id == task_id: self.tasks.pop(i); return True
        return False
    def get_board_state(self):
        return {s: [{"id":t.id, "title":t.title, "status":t.status, "assigned_to":t.assigned_to, "retries":t.retries} for t in self.tasks if t.status==s] for s in ["todo","in_progress","review","done"]}

# ============== SELF-VERIFIER ==============
class SelfVerifier:
    @staticmethod
    def verify_code(code: str, test_code: str) -> Dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir,"code.py").write_text(code); Path(tmpdir,"test_code.py").write_text(test_code)
            result = subprocess.run(["python","-m","pytest", tmpdir], capture_output=True, text=True)
            return {"passed": result.returncode==0, "output": result.stdout+result.stderr}

# ============== BROWSER ==============
class AdvancedBrowser:
    def __init__(self): self.page = None; self.browser = None
    async def start(self):
        from playwright.async_api import async_playwright
        p = await async_playwright().start(); self.browser = await p.chromium.launch(headless=False); self.page = await self.browser.new_page()
    async def goto(self, url: str):
        if not self.page: await self.start()
        await self.page.goto(url); return f"Navigated to {url}"
    async def click(self, selector: str): await self.page.click(selector); return f"Clicked {selector}"
    async def type_text(self, selector: str, text: str): await self.page.fill(selector, text); return f"Typed into {selector}"
    async def screenshot(self, path: str = "browser.png"): await self.page.screenshot(path=path); return f"Screenshot: {path}"
    async def close(self):
        if self.browser: await self.browser.close()

# ============== DESKTOP ==============
class DesktopController:
    @staticmethod
    def move_mouse(x: int, y: int):
        try: import pyautogui; pyautogui.moveTo(x,y); return f"Moved to ({x},{y})"
        except: return "PyAutoGUI not installed"
    @staticmethod
    def click():
        try: import pyautogui; pyautogui.click(); return "Clicked"
        except: return "PyAutoGUI not installed"
    @staticmethod
    def type_text(text: str):
        try: import pyautogui; pyautogui.write(text); return f"Typed: {text}"
        except: return "PyAutoGUI not installed"
    @staticmethod
    def press_key(key: str):
        try: import pyautogui; pyautogui.press(key); return f"Pressed: {key}"
        except: return "PyAutoGUI not installed"
    @staticmethod
    def open_app(app_name: str):
        import platform
        s = platform.system()
        if s == "Darwin": subprocess.Popen(["open", "-a", app_name])
        elif s == "Windows": subprocess.Popen(["start", app_name], shell=True)
        else: subprocess.Popen(["xdg-open", app_name])
        return f"Opened {app_name}"

# ============== SESSION STORE ==============
class SessionStore:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path; self._init_db()
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, messages TEXT, updated_at TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS skills (name TEXT PRIMARY KEY, content TEXT, created_at TEXT)")
    def load(self, session_id: str) -> Optional[List[Dict]]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT messages FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            return json.loads(row[0]) if row else None
    def save(self, session_id: str, messages: List[Dict]):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO sessions (session_id, messages, updated_at) VALUES (?, ?, ?)", (session_id, json.dumps(messages), now))
    def list_sessions(self) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [r[0] for r in conn.execute("SELECT session_id FROM sessions ORDER BY updated_at DESC").fetchall()]

# ============== CONTEXT COMPRESSION ==============
def compress_messages(messages: List[Dict], keep_recent: int = 5) -> List[Dict]:
    if len(messages) <= 20: return messages
    system_msg = messages.pop(0) if messages and messages[0]["role"] == "system" else None
    middle = messages[3:-keep_recent]; recent = messages[-keep_recent:]
    if not middle: return messages
    prompt = "Summarize this conversation concisely:\n" + "\n".join([f"{m['role']}: {str(m.get('content',''))[:200]}" for m in middle])
    try:
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":prompt}], max_tokens=200)
        summary = resp.choices[0].message.content
    except: summary = "[Conversation summarized]"
    compressed = messages[:3] + [{"role": "user", "content": f"[Summary]: {summary}"}] + recent
    if system_msg: compressed.insert(0, system_msg)
    return compressed

# ============== PLUGIN MARKETPLACE ==============
class PluginMarketplace:
    PLUGIN_MANIFEST_FIELDS = {"name", "version", "description", "author", "tools", "min_agent_version"}

    @staticmethod
    def discover_local() -> List[Dict]:
        plugins = []
        for p in PLUGINS_DIR.iterdir():
            manifest = p / "plugin.json"
            if manifest.exists():
                try:
                    data = json.loads(manifest.read_text())
                    if PluginMarketplace._validate_manifest(data):
                        data["path"] = str(p)
                        plugins.append(data)
                except: pass
        return plugins

    @staticmethod
    def _validate_manifest(data: dict) -> bool:
        return all(k in data for k in PluginMarketplace.PLUGIN_MANIFEST_FIELDS)

    @staticmethod
    def install_from_url(url: str, name: str = None) -> str:
        import urllib.request, zipfile, io
        try:
            resp = urllib.request.urlopen(url, timeout=30)
            zip_data = io.BytesIO(resp.read())
            target_name = name or url.rsplit("/", 1)[-1].replace(".zip", "")
            target_dir = PLUGINS_DIR / target_name
            target_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_data) as zf:
                zf.extractall(target_dir)
            manifest = target_dir / "plugin.json"
            if manifest.exists():
                data = json.loads(manifest.read_text())
                return f"✅ Installed plugin '{data.get('name', target_name)}' v{data.get('version', '0')}"
            return f"⚠️ Extracted to {target_dir} but no plugin.json found"
        except Exception as e:
            return f"❌ Install failed: {e}"

    @staticmethod
    def list_remote() -> List[Dict]:
        return [
            {"name": "web-scraper", "version": "1.0.0", "description": "Advanced web scraping with CSS selectors", "author": "Hermes"},
            {"name": "code-analyzer", "version": "1.1.0", "description": "Static analysis for Python, JS, Rust", "author": "Hermes"},
            {"name": "docker-compose", "version": "0.9.0", "description": "Multi-container Docker orchestration", "author": "Community"},
            {"name": "notion-sync", "version": "2.0.0", "description": "Sync agent memory with Notion", "author": "Community"},
        ]

    @staticmethod
    def get_skill_versions(name: str) -> List[Dict]:
        skill_path = SKILLS_DIR / f"{name}.md"
        versions = []
        if skill_path.exists():
            content = skill_path.read_text()
            versions.append({"version": "local", "path": str(skill_path), "size": len(content)})
        return versions

# ============== WEB SOCKET SERVER ==============
class WebSocketServer:
    def __init__(self, agent):
        self.agent = agent; self.clients = set()
    async def handler(self, websocket, path=None):
        self.clients.add(websocket)
        try:
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get("type", "chat")
                if msg_type == "chat":
                    result = self.agent.run_loop([{"role":"system","content":self.agent.system_prompt},{"role":"user","content":data["text"]}])
                    await websocket.send(json.dumps({"type":"response", "content":result}))
                elif msg_type == "command":
                    cmd = data["command"]
                    if cmd == "kanban":
                        await websocket.send(json.dumps({"type":"kanban", "data":self.agent.kanban.get_board_state()}))
                    elif cmd.startswith("kanban:add:"):
                        self.agent.kanban.add_task(cmd.split(":", 2)[2])
                        await websocket.send(json.dumps({"type":"kanban", "data":self.agent.kanban.get_board_state()}))
                    elif cmd.startswith("kanban:move:"):
                        parts = cmd.split(":")
                        self.agent.kanban.move_task(parts[2], parts[3])
                        await websocket.send(json.dumps({"type":"kanban", "data":self.agent.kanban.get_board_state()}))
                    elif cmd.startswith("kanban:remove:"):
                        self.agent.kanban.remove_task(cmd.split(":", 2)[2])
                        await websocket.send(json.dumps({"type":"kanban", "data":self.agent.kanban.get_board_state()}))
                    elif cmd == "tools":
                        await websocket.send(json.dumps({"type":"tools", "data":self.agent.registry.list_tools()}))
                    elif cmd == "skills":
                        await websocket.send(json.dumps({"type":"skills", "data":SelfLearner.load_skills()}))
                    elif cmd == "plugins":
                        await websocket.send(json.dumps({"type":"plugins", "data":PluginMarketplace.discover_local()}))
                    elif cmd == "remote-plugins":
                        await websocket.send(json.dumps({"type":"plugins", "data":PluginMarketplace.list_remote()}))
                    elif cmd.startswith("install-plugin:"):
                        url = cmd.split(":", 1)[1]
                        result = PluginMarketplace.install_from_url(url)
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("skill-versions:"):
                        name = cmd.split(":", 1)[1]
                        await websocket.send(json.dumps({"type":"skill-versions", "data":PluginMarketplace.get_skill_versions(name)}))
                    elif cmd.startswith("provider:"):
                        p = cmd.split(":")[1]
                        self.agent.provider = p
                        await websocket.send(json.dumps({"type":"provider", "data":p}))
                    elif cmd == "logs":
                        await websocket.send(json.dumps({"type":"logs", "data":self.agent.logs[-200:]}))
                    elif cmd == "logs:clear":
                        self.agent.logs.clear()
                        await websocket.send(json.dumps({"type":"logs", "data":[]}))
                    elif cmd.startswith("diff:"):
                        path = cmd.split(":", 1)[1] if ":" in cmd else ""
                        diff = _git_diff(path)
                        await websocket.send(json.dumps({"type":"diff", "data":diff}))
                    elif cmd == "diff":
                        diff = _git_diff()
                        await websocket.send(json.dumps({"type":"diff", "data":diff}))
                    elif cmd.startswith("lsp:"):
                        target = cmd.split(":", 1)[1]
                        diag = _pyright_diagnostics(target)
                        await websocket.send(json.dumps({"type":"lsp", "data":diag}))
                    elif cmd == "lsp":
                        diag = _pyright_diagnostics()
                        await websocket.send(json.dumps({"type":"lsp", "data":diag}))
                    elif cmd.startswith("provider:test:"):
                        p = cmd.split(":", 2)[2]
                        result = test_provider(p)
                        await websocket.send(json.dumps({"type":"provider_test_result", "data":result}))
                    elif cmd == "cost":
                        await websocket.send(json.dumps({"type":"cost", "data":_get_cost_summary()}))
                    # Send any streamed lines
                    stream = _drain_stream()
                    if stream:
                        await websocket.send(json.dumps({"type":"stream", "data":stream}))
        except: pass
        finally: self.clients.discard(websocket)
    async def start(self):
        import websockets
        async def _stream_pusher():
            while True:
                await asyncio.sleep(0.5)
                stream = _drain_stream()
                if stream and self.clients:
                    msg = json.dumps({"type":"stream", "data":stream})
                    await asyncio.gather(*(c.send(msg) for c in self.clients), return_exceptions=True)
        asyncio.ensure_future(_stream_pusher())
        async with websockets.serve(self.handler, WS_HOST, WS_PORT):
            print(f"🌐 WebSocket server on ws://{WS_HOST}:{WS_PORT}")
            await asyncio.Future()

# ============== ULTIMATE AGENT ==============
class UltimateAgent:
    def __init__(self):
        self.registry = registry; self.session_id = f"sess_{int(time.time())}"
        self.store = SessionStore(); self.messages = []
        self.goal_manager: Optional[GoalManager] = None; self.kanban = KanbanBoard()
        self.browser = AdvancedBrowser(); self.desktop = DesktopController()
        self.provider = LLM_PROVIDER
        self.logs: List[Dict] = []
        skills = SelfLearner.load_skills()
        skills_str = f"\nAvailable skills: {', '.join(skills)}" if skills else ""
        self.system_prompt = f"You are Hermes-Ultimate, an autonomous coding assistant with tools for files, shell, Docker, browser, and desktop. You can spawn sub-agents, verify code, and learn new skills. Be concise and self-correcting.{skills_str}"
        self._register_advanced_tools()
    def _register_advanced_tools(self):
        @self.registry.register(description="Spawn a sub-agent for a task")
        def spawn_subagent(name: str, task: str) -> str:
            return SubAgent(name, task, str(self.messages[-3:])).run(self)
        @self.registry.register(description="Write, test, and save code")
        def implement_feature(code: str, test_code: str = "") -> str:
            if test_code:
                v = SelfVerifier.verify_code(code, test_code)
                if not v["passed"]: return f"Verification failed: {v['output']}"
            result = registry.execute("execute_python", {"code": code})
            if "Error" not in result: SelfLearner.save_skill(f"impl_{int(time.time())}", code[:50], [{"code": code}])
            return result
        @self.registry.register(description="Start recording a demo for a skill")
        def start_demo(name: str, description: str) -> str:
            SelfLearner.start_recording(name, description); return f"Recording '{name}'"
        @self.registry.register(description="Stop recording and generate skill")
        def stop_demo() -> str: return SelfLearner.stop_recording(self.provider)
        @self.registry.register(description="Chain multiple skills into a composed workflow")
        def compose_workflow(skill_names_json: str, goal: str) -> str:
            return SelfLearner.compose_workflow(json.loads(skill_names_json), goal, self.provider)
        @self.registry.register(description="Browser control: goto, click, type, screenshot")
        def browser_action(action: str, url: str = "", selector: str = "", text: str = "") -> str:
            m = {"goto": lambda: asyncio.run(self.browser.goto(url)), "click": lambda: asyncio.run(self.browser.click(selector)),
                 "type": lambda: asyncio.run(self.browser.type_text(selector, text)), "screenshot": lambda: asyncio.run(self.browser.screenshot())}
            return m.get(action, lambda: "Unknown")()
        @self.registry.register(description="Desktop control: move_mouse, click, type, key, open_app")
        def desktop_action(action: str, x: int = 0, y: int = 0, text: str = "", key: str = "", app: str = "") -> str:
            m = {"move_mouse": lambda: DesktopController.move_mouse(x,y), "click": DesktopController.click,
                 "type": lambda: DesktopController.type_text(text), "key": lambda: DesktopController.press_key(key),
                 "open_app": lambda: DesktopController.open_app(app or text)}
            return m.get(action, lambda: "Unknown")()
        @self.registry.register(description="Run parallel sub-agents. Provide JSON array of {name, prompt}")
        def multitask(tasks_json: str) -> str:
            return json.dumps(ParallelExecutor.run(json.loads(tasks_json), self), indent=2)
        @self.registry.register(description="Switch LLM provider at runtime")
        def switch_provider(provider: str) -> str:
            if provider in PROVIDER_CONFIGS: self.provider = provider; return f"Switched to {provider}"
            return f"Unknown provider. Available: {', '.join(PROVIDER_CONFIGS.keys())}"
        @self.registry.register(description="List all available providers")
        def list_providers() -> str: return ", ".join(PROVIDER_CONFIGS.keys())
        @self.registry.register(description="Show kanban board")
        def show_kanban() -> str: return json.dumps(self.kanban.get_board_state(), indent=2)
    def run_loop(self, messages: List[Dict] = None, max_iters: int = 10) -> str:
        if messages is None: messages = [{"role": "system", "content": self.system_prompt}]
        self.messages = messages.copy()
        self.logs.append({"type": "run_start", "timestamp": time.time(), "iterations": 0})
        for i in range(max_iters):
            self.logs.append({"type": "llm_call", "iteration": i, "timestamp": time.time()})
            schemas = self.registry.get_openai_schemas()
            try: response = ProviderRouter.call(self.messages, schemas, self.provider)
            except Exception as e: return f"LLM Error: {e}"
            self.messages.append({"role": "assistant", "content": response.content or ""})
            tcs = getattr(response, "tool_calls", [])
            if not tcs:
                self.logs.append({"type": "llm_response", "iteration": i, "content": (response.content or "")[:200]})
                return response.content or "No response"
            calls = []
            for tc in tcs:
                f = tc.function if hasattr(tc, "function") else tc["function"]
                calls.append({"id": tc.id if hasattr(tc,"id") else tc["id"], "name": f.name if hasattr(f,"name") else f["name"], "args": json.loads(f.arguments if hasattr(f,"arguments") else f["arguments"])})
            results = self.registry.execute_parallel(calls)
            for c in calls:
                self.logs.append({"type": "tool_call", "name": c["name"], "args": c["args"], "iteration": i, "timestamp": time.time()})
            for res in results:
                self.logs.append({"type": "tool_result", "id": res["id"], "result": res["result"][:200], "iteration": i, "timestamp": time.time()})
            if SelfLearner._recording:
                for res in results:
                    for c in calls:
                        if c["id"] == res["id"]: SelfLearner.record_action(c["name"], c["args"]); break
            for res in results:
                self.messages.append({"role": "tool", "tool_call_id": res["id"], "content": res["result"]})
            if self.goal_manager and self.goal_manager.is_complete(response.content or ""): return f"Goal Complete: {self.goal_manager.goal}"
        return "Max iterations reached."
    def chat(self):
        import uuid
        sid = self.session_id
        print(f"🤖 Hermes-Ultimate | Session: {sid} | Provider: {self.provider} ({MODEL_NAME})")
        print(f"🔧 Available providers: {', '.join(PROVIDER_CONFIGS.keys())}")
        print("Commands: /goal, /multitask, /kanban, /browser, /desktop, /record, /compose, /provider, exit\n")
        while True:
            try: u = input("You: ").strip()
            except (EOFError, KeyboardInterrupt): break
            if not u: continue
            if u == "exit": break
            if u == "/reset":
                self.messages = [{"role":"system","content":self.system_prompt}]; print("🧹 Reset."); continue
            if u.startswith("/provider"):
                p = u.split(" ", 1)[1] if " " in u else ""
                if p in PROVIDER_CONFIGS: self.provider = p; print(f"✅ Switched to {p}")
                else: print(f"Available: {', '.join(PROVIDER_CONFIGS.keys())}")
                continue
            if u.startswith("/kanban"):
                parts = u.split()
                if len(parts) >= 3 and parts[1]=="add": t=self.kanban.add_task(parts[2]); print(f"✅ {t.id}"); continue
                if len(parts)>=2 and parts[1]=="show": print(json.dumps(self.kanban.get_board_state(), indent=2)); continue
            if u.startswith("/goal"):
                self.goal_manager = GoalManager(u.split(" ",1)[1])
                result = self.run_loop([{"role":"system","content":self.system_prompt},{"role":"user","content":f"Goal: {self.goal_manager.goal}. Work until COMPLETE."}])
                print(f"\n🤖 {result}\n"); continue
            if u.startswith("/multitask"):
                rest = u.split(" ",1)[1] if " " in u else ""
                tasks = [{"name":f"T-{i}","prompt":t.strip()} for i,t in enumerate(rest.split(",") if "," in rest else rest.split("|"))]
                print(json.dumps(ParallelExecutor.run(tasks, self), indent=2)); continue
            if u.startswith("/browser"):
                p = u.split(" ",2)
                if len(p)==3 and p[1]=="goto": print(asyncio.run(self.browser.goto(p[2])))
                elif len(p)>=2 and p[1]=="screenshot": print(asyncio.run(self.browser.screenshot()))
                continue
            if u.startswith("/desktop open"):
                print(DesktopController.open_app(u.split("open ",1)[1])); continue
            if u.startswith("/record"):
                parts = u.split(" ",2)
                if len(parts)>=3: SelfLearner.start_recording(parts[1], parts[2]); print(f"🔴 Recording '{parts[1]}'"); continue
                else: print("Usage: /record <name> <desc>"); continue
            if u == "/stop_record": print(SelfLearner.stop_recording(self.provider)); continue
            if u.startswith("/compose"):
                rest = u.split(" ",2)
                if len(rest)>=3:
                    names = [s.strip() for s in rest[1].split(",")]
                    print(SelfLearner.compose_workflow(names, rest[2], self.provider)); continue
            result = self.run_loop([{"role":"system","content":self.system_prompt},{"role":"user","content":u}])
            print(f"\n🤖 {result}\n")

async def run_ws_server(agent):
    ws = WebSocketServer(agent)
    await ws.start()

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "cli"
    agent = UltimateAgent()
    if mode == "ws":
        print(f"Starting WebSocket server on ws://{WS_HOST}:{WS_PORT}...")
        asyncio.run(run_ws_server(agent))
    elif mode == "cli":
        agent.chat()
    else:
        print("Usage: python agent_ultimate.py [cli|ws]")
