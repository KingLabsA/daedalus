#!/usr/bin/env python3
import os, sys, json, sqlite3, subprocess, threading, time, asyncio, tempfile, inspect, importlib, io, uuid, socket, hashlib, re
from pathlib import Path
import shutil
from typing import List, Dict, Any, Optional, Callable, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

# ============== SECURITY CONSTRAINTS ==============
BLOCKED_COMMANDS = ["rm -rf /", "sudo rm -rf", "format ", "mkfs", "dd if=", ":(){ :|:& };:"]
MAX_FILE_SIZE = 500_000
PROMPT_INJECTION_PATTERNS = [
    "ignore all previous", "disregard", "forget your instructions",
    "you are now", "act as", "system prompt", "new instructions",
    "override", "pretend you are", "you must obey",
]

def _is_safe_command(cmd: str) -> bool:
    lowered = cmd.lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked in lowered:
            return False
    return True

def _check_prompt_injection(text: str) -> bool:
    lowered = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern in lowered:
            return True
    return False

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
MAX_MESSAGES = 30
DB_FILE = os.getenv("DB_FILE", "hermes_ultimate.db")
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", ".hermes/skills"))
SKILLS_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINTS_DIR = Path(os.getenv("CHECKPOINTS_DIR", ".hermes/checkpoints"))
CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
WS_HOST = os.getenv("WS_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
PLUGINS_DIR = Path(os.getenv("PLUGINS_DIR", "plugins"))
PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
PLUGIN_REGISTRY_URL = os.getenv("PLUGIN_REGISTRY_URL", "https://hermes-plugins.fake")
SAFETY_MODE = os.getenv("SAFETY_MODE", "suggest")  # suggest, plan, auto

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
    "openai": {"input": 0.00025, "output": 0.001},
    "anthropic": {"input": 0.003, "output": 0.015},
    "groq": {"input": 0.0001, "output": 0.0001},
    "mistral": {"input": 0.0002, "output": 0.0006},
    "google": {"input": 0.000125, "output": 0.0005},
    "deepseek": {"input": 0.00014, "output": 0.00028},
    "together": {"input": 0.0001, "output": 0.0001},
    "fireworks": {"input": 0.0001, "output": 0.0001},
    "xai": {"input": 0.00015, "output": 0.0006},
    "perplexity": {"input": 0.0003, "output": 0.0015},
    "novita": {"input": 0.0001, "output": 0.0002},
    "openrouter": {"input": 0.0001, "output": 0.0001},
    "zhipu": {"input": 0.0001, "output": 0.0001},
    "moonshot": {"input": 0.0001, "output": 0.0001},
    "cohere": {"input": 0.00015, "output": 0.0006},
    "ollama": {"input": 0, "output": 0},
    "bedrock": {"input": 0.0008, "output": 0.0032},
    "azure": {"input": 0.00025, "output": 0.001},
    "huggingface": {"input": 0.0001, "output": 0.0001},
    "replicate": {"input": 0.0001, "output": 0.0001},
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
    if len(content) > MAX_FILE_SIZE:
        return f"Error: File too large ({len(content)} bytes). Max: {MAX_FILE_SIZE}"
    fp = os.path.expanduser(filepath)
    before = ""
    if os.path.exists(fp):
        with open(fp) as f: before = f.read()
    with open(fp, "w") as f: f.write(content)
    diff = ""
    if before and before != content:
        diff = _git_diff(filepath)
    _git_stage(fp)
    _push_stream({"type":"file_written","filepath":filepath})
    return f"Written to {filepath}"

@registry.register(description="Append content to a file (auto-stages changes)")
def append_file(filepath: str, content: str) -> str:
    if len(content) > MAX_FILE_SIZE:
        return f"Error: Content too large ({len(content)} bytes). Max: {MAX_FILE_SIZE}"
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

@registry.register(description="Run a shell command. Streams output live. Use use_docker=false for local execution.")
def run_command(command: str, working_dir: str = "", use_docker: str = "false", image: str = "python:3.12-slim") -> str:
    if not _is_safe_command(command):
        return f"Error: Command blocked for security: {command[:80]}"
    use_docker_flag = use_docker.lower() == "true" if isinstance(use_docker, str) else bool(use_docker)
    if use_docker_flag and shutil.which("docker"):
        cname = f"hermes-{uuid.uuid4().hex[:8]}"
        mount = f"{os.getcwd()}:/workspace"
        wd = working_dir or "/workspace"
        cmd = f'docker run --rm --name {cname} -v "{mount}" -w {wd} {image} sh -c "{command}"'
    else:
        cmd = command
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=working_dir or None, timeout=120)
        output = proc.stdout + proc.stderr
        _push_stream({"type":"stream","line":output[-2000:] if len(output) > 2000 else output})
        return output or f"Exit code: {proc.returncode}"
    except subprocess.TimeoutExpired:
        return f"Command timed out after 120s: {command[:80]}"
    except Exception as e:
        return f"Error: {e}"

@registry.register(description="Ripgrep-powered code search. Supports regex, file filters, case-insensitive. Returns file:line:content.")
def grep(pattern: str, path: str = ".", include: str = "", ignore_case: str = "false", max_results: str = "50") -> str:
    cmd_parts = ["rg", "--no-heading", "--color=never", "-n"]
    if ignore_case.lower() in ("true", "1", "yes"):
        cmd_parts.append("-i")
    if include:
        cmd_parts.extend(["-g", include])
    cmd_parts.extend(["--max-count", "5"])
    cmd_parts.append(pattern)
    cmd_parts.append(path)
    try:
        result = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=15)
        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        if len(lines) > int(max_results):
            lines = lines[:int(max_results)] + [f"... ({len(lines) - int(max_results)} more matches)"]
        return "\n".join(lines) if lines else "No matches."
    except FileNotFoundError:
        fallback = subprocess.run(
            f'grep -r -n "{pattern}" {path} 2>/dev/null | head -{max_results}',
            shell=True, capture_output=True, text=True
        )
        return fallback.stdout.strip() or "No matches (ripgrep not installed)."
    except subprocess.TimeoutExpired:
        return "Search timed out (>15s). Try a narrower path or pattern."
    except Exception as e:
        return f"Search error: {e}"

@registry.register(description="Rename a symbol across all files in the project. Performs safe text replacement.")
def rename_symbol(old_name: str, new_name: str, path: str = ".", include: str = "") -> str:
    cmd_parts = ["rg", "-l", "--color=never"]
    if include:
        cmd_parts.extend(["-g", include])
    cmd_parts.extend(["-w", old_name, path])
    try:
        result = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=15)
        files = [f for f in result.stdout.strip().split("\n") if f]
    except FileNotFoundError:
        fallback = subprocess.run(
            f'grep -r -l -w "{old_name}" {path} 2>/dev/null',
            shell=True, capture_output=True, text=True
        )
        files = [f for f in fallback.stdout.strip().split("\n") if f]
    except Exception as e:
        return f"Search error: {e}"
    if not files:
        return f"No files contain '{old_name}'"
    changed = []
    for filepath in files:
        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="ignore")
            count = len(re.findall(r'\b' + re.escape(old_name) + r'\b', content))
            if count > 0:
                new_content = re.sub(r'\b' + re.escape(old_name) + r'\b', new_name, content)
                Path(filepath).write_text(new_content, encoding="utf-8")
                changed.append(f"{filepath} ({count} occurrences)")
        except Exception as e:
            changed.append(f"{filepath} (error: {e})")
    summary = f"Renamed '{old_name}' → '{new_name}' in {len(changed)} files:\n"
    summary += "\n".join(f"  {c}" for c in changed)
    return summary

@registry.register(description="Explain a code snippet — describes what it does, inputs, outputs, and side effects.")
def explain_code(code: str) -> str:
    lines = code.strip().split("\n")
    findings = []
    findings.append(f"Lines: {len(lines)}")
    imports = [l.strip() for l in lines if l.strip().startswith(("import ", "from "))]
    if imports:
        findings.append(f"Imports: {', '.join(imports)}")
    funcs = [l.strip() for l in lines if l.strip().startswith("def ")]
    classes = [l.strip() for l in lines if l.strip().startswith("class ")]
    if funcs:
        findings.append(f"Functions: {', '.join(f.split('(')[0].replace('def ', '') for f in funcs)}")
    if classes:
        findings.append(f"Classes: {', '.join(c.split('(')[0].split(':')[0].replace('class ', '') for c in classes)}")
    has_return = any("return " in l for l in lines)
    has_print = any("print(" in l for l in lines)
    has_yield = any("yield " in l for l in lines)
    side_effects = []
    if has_print: side_effects.append("prints to stdout")
    if has_yield: side_effects.append("is a generator")
    if any("open(" in l for l in lines): side_effects.append("reads/writes files")
    if any("subprocess" in l or "os.system" in l for l in lines): side_effects.append("runs external commands")
    if any("requests." in l for l in lines): side_effects.append("makes HTTP requests")
    if side_effects:
        findings.append(f"Side effects: {', '.join(side_effects)}")
    if has_return:
        returns = [l.strip() for l in lines if "return " in l and not l.strip().startswith("#")]
        if returns:
            findings.append(f"Returns: {returns[0].strip()[:100]}")
    return "\n".join(findings)

@registry.register(description="Review code for bugs, security issues, and improvements. Returns structured findings.")
def review_code(code: str, filepath: str = "") -> str:
    findings = []
    lines = code.strip().split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "eval(" in stripped or "exec(" in stripped:
            findings.append(f"L{i}: SECURITY — use of eval/exec: {stripped[:80]}")
        if "os.system(" in stripped:
            findings.append(f"L{i}: SECURITY — os.system is unsafe, use subprocess: {stripped[:80]}")
        if "pickle.load" in stripped:
            findings.append(f"L{i}: SECURITY — pickle.load on untrusted data: {stripped[:80]}")
        if "shell=True" in stripped:
            findings.append(f"L{i}: WARNING — subprocess with shell=True: {stripped[:80]}")
        if "except:" in stripped or "except Exception:" in stripped:
            findings.append(f"L{i}: STYLE — bare except clause: {stripped[:80]}")
        if "TODO" in stripped or "FIXME" in stripped or "HACK" in stripped:
            findings.append(f"L{i}: NOTE — unresolved marker: {stripped[:80]}")
        if "import *" in stripped:
            findings.append(f"L{i}: STYLE — wildcard import: {stripped[:80]}")
        if len(stripped) > 120:
            findings.append(f"L{i}: STYLE — line >120 chars ({len(stripped)}): {stripped[:60]}...")
    if not lines:
        findings.append("Empty code — nothing to review.")
    elif not findings:
        findings.append("No issues found.")
    header = f"Code review for {filepath or '<snippet>'} ({len(lines)} lines, {len(findings)} findings)"
    return header + "\n" + "\n".join(findings)

@registry.register(description="Suggest safe refactorings for code: extract functions, simplify conditionals, reduce nesting.")
def refactor_code(code: str) -> str:
    lines = code.strip().split("\n")
    suggestions = []
    depth = 0
    func_count = 0
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("def "):
            func_count += 1
        if stripped.startswith(("if ", "for ", "while ", "with ", "try:", "except")):
            depth = max(depth, line.count("    ") + 1)
        if stripped.startswith("def ") and i > 10:
            prev = lines[max(0, i-10):i]
            if any(l.strip().startswith(("if ", "for ")) for l in prev):
                suggestions.append(f"L{i}: Consider extracting the logic before '{stripped[:50]}' into a helper function")
    if depth > 4:
        suggestions.append(f"Nesting depth {depth} detected — consider extracting inner blocks into functions")
    if func_count == 0 and len(lines) > 30:
        suggestions.append("No functions defined in 30+ lines — consider extracting reusable blocks into functions")
    long_lines = [(i+1, l) for i, l in enumerate(lines) if len(l.strip()) > 100 and not l.strip().startswith("#")]
    if long_lines:
        suggestions.append(f"{len(long_lines)} lines exceed 100 chars — consider breaking them up")
    if not suggestions:
        suggestions.append("Code looks clean — no refactoring suggestions.")
    return f"Refactoring suggestions ({len(suggestions)}):\n" + "\n".join(f"  • {s}" for s in suggestions)

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
        args = json.loads(arguments)
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
        model = cfg.get("default_model", "gpt-4o-mini")
        resp = client.chat.completions.create(model=model, messages=[{"role":"user","content":"say ok"}], max_tokens=10)
        lat = round(time.time() - t0, 2)
        return f"{target}: OK ({lat}s) via {cfg.get('default_model','?')}"
    except Exception as e:
        return f"{target}: FAIL ({e})"

# ============== NEW TOOLS: Git Branch, File Explorer, Suggest/Approve ==============

_pending_writes: Dict[str, Dict] = {}

@registry.register(description="Push committed changes to remote. Specify branch (default: current).")
def git_push(branch: str = "") -> str:
    try:
        target = branch or subprocess.run(["git","rev-parse","--abbrev-ref","HEAD"], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["git","push","origin", target], capture_output=True, text=True, cwd=os.getcwd())
        return f"Pushed {target}: {r.stdout.strip() or r.stderr.strip() or 'OK'}"
    except Exception as e: return f"git_push error: {e}"

@registry.register(description="List branches, create new branch, or switch branch.")
def git_branch(name: str = "", switch: str = "false") -> str:
    try:
        if name:
            r = subprocess.run(["git","branch", name], capture_output=True, text=True, cwd=os.getcwd())
            if switch.lower() == "true":
                r2 = subprocess.run(["git","checkout", name], capture_output=True, text=True, cwd=os.getcwd())
                return f"Created and switched to {name}: {r2.stdout.strip() or r2.stderr.strip() or 'OK'}"
            return f"Created branch {name}: {r.stdout.strip() or r.stderr.strip() or 'OK'}"
        r = subprocess.run(["git","branch","-a"], capture_output=True, text=True, cwd=os.getcwd())
        return r.stdout.strip() or "(no branches)"
    except Exception as e: return f"git_branch error: {e}"

@registry.register(description="Show git log (last N commits with stats).")
def git_log(n: str = "10") -> str:
    try:
        r = subprocess.run(["git","log",f"--oneline","--stat","-n", str(n)], capture_output=True, text=True, cwd=os.getcwd())
        return r.stdout.strip() or "(no commits)"
    except Exception as e: return f"git_log error: {e}"

@registry.register(description="List files and directories at a path. Returns JSON tree.")
def list_files(path: str = ".", max_depth: str = "3") -> str:
    try:
        root = Path(path).resolve()
        result = {"name": root.name, "type": "dir", "children": []}
        depth = int(max_depth)
        def _scan(d: Path, current_depth: int):
            if current_depth >= depth: return []
            children = []
            try:
                for item in sorted(d.iterdir()):
                    if item.name.startswith(".") and item.name not in (".github", ".env.example"): continue
                    if item.name in ("node_modules", "__pycache__", "target", ".git", "dist", "build"): continue
                    if item.is_dir():
                        children.append({"name": item.name, "type": "dir", "children": _scan(item, current_depth + 1)})
                    else:
                        size = item.stat().st_size
                        children.append({"name": item.name, "type": "file", "size": size})
            except PermissionError: pass
            return children
        result["children"] = _scan(root, 0)
        return json.dumps(result, indent=2)
    except Exception as e: return f"list_files error: {e}"

@registry.register(description="Suggest a file write for user approval. Returns diff preview. User must confirm with confirm_write.")
def suggest_write(filepath: str, content: str) -> str:
    try:
        p = Path(filepath)
        old_content = p.read_text() if p.exists() else ""
        import difflib
        old_lines = old_content.splitlines(keepends=True)
        new_lines = content.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{filepath}", tofile=f"b/{filepath}", lineterm=""))
        diff_text = "\n".join(diff_lines) if diff_lines else "(no changes)"
        write_id = f"w-{int(time.time()*1000)}"
        _pending_writes[write_id] = {"filepath": filepath, "content": content}
        return json.dumps({"id": write_id, "filepath": filepath, "diff": diff_text, "status": "pending_approval"})
    except Exception as e: return f"suggest_write error: {e}"

@registry.register(description="Confirm and execute a pending file write (from suggest_write). Pass the write ID.")
def confirm_write(write_id: str) -> str:
    pending = _pending_writes.get(write_id)
    if not pending: return f"No pending write with id {write_id}"
    filepath = pending["filepath"]
    content = pending["content"]
    del _pending_writes[write_id]
    try:
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        subprocess.run(["git","add", filepath], capture_output=True, text=True)
        return f"Written {filepath} ({len(content)} chars)"
    except Exception as e: return f"confirm_write error: {e}"

@registry.register(description="Discard a pending file write (from suggest_write).")
def deny_write(write_id: str) -> str:
    if write_id in _pending_writes:
        del _pending_writes[write_id]
        return f"Denied write {write_id}"
    return f"No pending write with id {write_id}"


_bg_processes: Dict[str, subprocess.Popen] = {}
_bg_output: Dict[str, list] = {}
_bg_counter = 0

@registry.register(description="Run a command in the background. Returns a process ID for polling/killing.")
def background_process(command: str, workdir: str = "") -> str:
    global _bg_counter
    _bg_counter += 1
    pid = f"bg-{_bg_counter}"
    try:
        proc = subprocess.Popen(
            command, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=workdir or None
        )
        _bg_processes[pid] = proc
        _bg_output[pid] = []
        def _reader():
            for line in proc.stdout:
                _bg_output[pid].append(line.rstrip("\n"))
        threading.Thread(target=_reader, daemon=True).start()
        return f"Started background process {pid}: {command}"
    except Exception as e:
        return f"Error starting background process: {e}"

@registry.register(description="Get output from a background process. Pass pid from background_process.")
def poll_process(pid: str) -> str:
    if pid not in _bg_processes:
        return f"Unknown process: {pid}"
    proc = _bg_processes[pid]
    output = "\n".join(_bg_output.get(pid, []))
    if proc.poll() is not None:
        del _bg_processes[pid]
        return f"{output}\n[exited with code {proc.returncode}]" if output else f"[exited with code {proc.returncode}]"
    return output if output else f"[running, no output yet]"

@registry.register(description="Kill a background process by its pid.")
def kill_process(pid: str) -> str:
    if pid not in _bg_processes:
        return f"Unknown process: {pid}"
    proc = _bg_processes[pid]
    proc.terminate()
    del _bg_processes[pid]
    return f"Terminated process {pid}"

@registry.register(description="Run project linter and tests. Auto-detects Python (ruff+pytest) or Node (eslint+test).")
def lint_and_test(path: str = ".") -> str:
    results = []
    p = Path(path)
    py_files = list(p.rglob("*.py"))[:5]
    if py_files or (p / "pyproject.toml").exists():
        try:
            ruff = subprocess.run(["ruff", "check", str(p)], capture_output=True, text=True, timeout=30)
            results.append(f"ruff: {"PASS" if ruff.returncode == 0 else "FAIL"}\n{ruff.stdout.strip()[:500]}")
        except FileNotFoundError:
            results.append("ruff: NOT INSTALLED")
        try:
            pytest_r = subprocess.run(["python", "-m", "pytest", str(p), "-x", "-q"], capture_output=True, text=True, timeout=60)
            results.append(f"pytest: {"PASS" if pytest_r.returncode == 0 else "FAIL"}\n{pytest_r.stdout.strip()[:500]}")
        except Exception as e:
            results.append(f"pytest: ERROR {e}")
    js_files = list(p.rglob("*.ts")) + list(p.rglob("*.tsx")) + list(p.rglob("*.js"))
    if js_files and (p / "package.json").exists():
        try:
            eslint = subprocess.run(["npx", "eslint", str(p)], capture_output=True, text=True, timeout=30)
            results.append(f"eslint: {"PASS" if eslint.returncode == 0 else "FAIL"}\n{eslint.stdout.strip()[:500]}")
        except Exception:
            results.append("eslint: NOT AVAILABLE")
        try:
            npm_test = subprocess.run(["npm", "test"], capture_output=True, text=True, timeout=60, cwd=str(p))
            results.append(f"npm test: {"PASS" if npm_test.returncode == 0 else "FAIL"}\n{npm_test.stdout.strip()[:500]}")
        except Exception:
            results.append("npm test: NOT AVAILABLE")
    return "\n\n".join(results) if results else f"No lint/test config found at {path}"

@registry.register(description="Kanban task board tool. Actions: list, add, move, remove.")
def task_board(action: str = "list", task_name: str = "", column: str = "") -> str:
    board = KanbanBoard()
    if action == "list":
        return json.dumps(board.get_board_state(), indent=1)
    elif action == "add" and task_name:
        task = board.add_task(task_name)
        return f"Added task: {task.id} - {task.title}"
    elif action == "move" and task_name:
        board.move_task(task_name, column or "done")
        return f"Moved task {task_name} to {column or "done"}"
    elif action == "remove" and task_name:
        board.remove_task(task_name)
        return f"Removed task: {task_name}"
    return f"Unknown task action: {action}"

@registry.register(description="Map repository structure: functions, classes, imports per file. Returns JSON symbol index.")
def repo_map(path: str = ".") -> str:
    symbols = {}
    p = Path(path)
    count = 0
    for f in sorted(p.rglob("*.py")):
        if count >= 500 or ".git" in str(f) or "node_modules" in str(f):
            break
        try:
            content_f = f.read_text()[:10000]
            syms = {"functions": [], "classes": [], "imports": []}
            for line in content_f.split("\n"):
                stripped = line.strip()
                if stripped.startswith("def "):
                    syms["functions"].append(stripped.split("(")[0].replace("def ", ""))
                elif stripped.startswith("class "):
                    syms["classes"].append(stripped.split("(")[0].split(":")[0].replace("class ", ""))
                elif stripped.startswith("import ") or stripped.startswith("from "):
                    syms["imports"].append(stripped[:80])
            if any(syms.values()):
                symbols[str(f.relative_to(p))] = syms
                count += 1
        except: pass
    return json.dumps(symbols, indent=1)[:10000]

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

# ============== SELF-HEALER ==============
class SelfHealer:
    @staticmethod
    def analyze_error(error: str, context: str = "") -> str:
        """Analyze an error and suggest a fix strategy."""
        error_lower = error.lower()
        if "import" in error_lower or "module" in error_lower:
            return f"Import error detected. Try: pip install <missing-module> or check sys.path. Context: {context[:200]}"
        elif "syntax" in error_lower:
            return f"Syntax error. Check for missing colons, brackets, or indentation. Context: {context[:200]}"
        elif "type" in error_lower and "error" in error_lower:
            return f"Type error. Check variable types and function signatures. Context: {context[:200]}"
        elif "attribute" in error_lower:
            return f"Attribute error. The object may not have this method/property. Context: {context[:200]}"
        elif "timeout" in error_lower:
            return f"Timeout error. The operation took too long. Try increasing timeout or simplifying. Context: {context[:200]}"
        elif "connection" in error_lower or "network" in error_lower:
            return f"Network error. Check connectivity and API endpoints. Context: {context[:200]}"
        elif "permission" in error_lower or "access" in error_lower:
            return f"Permission error. Check file permissions and API keys. Context: {context[:200]}"
        else:
            return f"Unknown error pattern. Manual investigation needed. Context: {context[:200]}"

    @staticmethod
    def auto_fix(tool_name: str, error: str, args: dict) -> Optional[Dict]:
        """Attempt automatic fix for known error patterns. Returns fixed args or None."""
        error_lower = error.lower()
        if tool_name == "run_command" and "not found" in error_lower:
            # Try without docker if docker command not found
            if "docker" in error_lower:
                return {"command": args.get("command", ""), "use_docker": "false"}
        if tool_name == "read_file" and ("no such file" in error_lower or "not found" in error_lower):
            # Try common variations
            fp = args.get("filepath", "")
            alternatives = [fp + ".py", fp + ".js", fp + ".ts", fp.replace(".py", ".tsx")]
            for alt in alternatives:
                if os.path.exists(os.path.expanduser(alt)):
                    return {"filepath": alt}
        return None

# ============== SELF-LEARNER ==============
class SelfLearner:
    _recording = False; _recording_name = ""; _recording_description = ""; _action_log = []
    @classmethod
    def start_recording(cls, name: str, description: str):
        cls._recording = True; cls._recording_name = name; cls._recording_description = description
        cls._action_log = []; print(f"Recording: '{name}'")
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
            cfg = PROVIDER_CONFIGS.get(provider, {})
            model = cfg.get("default_model", "gpt-4o-mini")
            resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], temperature=0.2)
            skill_md = resp.choices[0].message.content
        except Exception as e:
            skill_md = f"---\nname: {cls._recording_name}\ndescription: {cls._recording_description}\n---\n{json.dumps(cls._action_log, indent=2)}"
        skill_path = SKILLS_DIR / f"{cls._recording_name}.md"
        skill_path.write_text(skill_md); cls._action_log = []
        return f"Skill saved: {skill_path}\n{skill_md}"
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
            cfg = PROVIDER_CONFIGS.get(provider, {})
            model = cfg.get("default_model", "gpt-4o-mini")
            resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], temperature=0.3)
            workflow = resp.choices[0].message.content
        except Exception as e: return f"Compose failed: {e}"
        path = SKILLS_DIR / f"composed_{int(time.time())}.md"
        path.write_text(workflow)
        return f"Composed workflow saved: {path}\n{workflow}"
    @staticmethod
    def save_skill(name: str, description: str, workflow: list):
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        (SKILLS_DIR / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{json.dumps(workflow, indent=2)}")
    @staticmethod
    def load_skills() -> list: return [f.stem for f in SKILLS_DIR.glob("*.md")]

# ============== HOOKS / LIFECYCLE SYSTEM ==============
class HookManager:
    """Lifecycle hook system. Register callbacks for tool pre/post, LLM pre/post, etc."""
    _hooks: Dict[str, List[Callable]] = {
        "pre_tool": [], "post_tool": [], "pre_llm": [], "post_llm": [],
        "pre_commit": [], "post_commit": [], "on_error": [], "on_start": [], "on_stop": [],
    }

    @classmethod
    def register(cls, event: str, callback: Callable):
        if event in cls._hooks:
            cls._hooks[event].append(callback)

    @classmethod
    def unregister(cls, event: str, callback: Callable):
        if event in cls._hooks and callback in cls._hooks[event]:
            cls._hooks[event].remove(callback)

    @classmethod
    def fire(cls, event: str, **kwargs) -> List[Any]:
        results = []
        for cb in cls._hooks.get(event, []):
            try:
                result = cb(**kwargs) if kwargs else cb()
                results.append(result)
            except Exception as e:
                print(f"Hook error ({event}): {e}")
        return results

    @classmethod
    def list_hooks(cls) -> Dict[str, int]:
        return {event: len(cbs) for event, cbs in cls._hooks.items()}

# ============== FILE WATCHER ==============
class FileWatcher:
    """Watches the project directory for changes and auto-reindexes."""
    _observer = None
    _indexer = None
    _debounce_seconds = 2.0
    _last_reindex = 0.0
    _changed_files: set = set()

    @classmethod
    def start(cls, indexer=None, path: str = "."):
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            return "watchdog not installed — file watcher disabled"
        if cls._observer and cls._observer.is_alive():
            return "File watcher already running"
        cls._indexer = indexer
        _IGNORED = {".git", "__pycache__", "node_modules", ".hermes", ".pytest_cache", "dist", "build", ".venv"}

        class ChangeHandler(FileSystemEventHandler):
            def on_modified(self, event):
                cls._track(event.src_path)
            def on_created(self, event):
                cls._track(event.src_path)
            def on_deleted(self, event):
                cls._track(event.src_path)

        handler = ChangeHandler()
        cls._observer = Observer()
        cls._observer.schedule(handler, path, recursive=True)
        cls._observer.daemon = True
        cls._observer.start()
        return "File watcher started"

    @classmethod
    def _track(cls, filepath: str):
        from watchdog.events import FileSystemEventHandler
        _IGNORED = {".git", "__pycache__", "node_modules", ".hermes", ".pytest_cache", "dist", "build", ".venv"}
        parts = Path(filepath).parts
        if any(p in _IGNORED for p in parts):
            return
        cls._changed_files.add(filepath)
        now = time.time()
        if now - cls._last_reindex > cls._debounce_seconds and len(cls._changed_files) > 2:
            cls._last_reindex = now
            cls._changed_files.clear()
            if cls._indexer:
                HookManager.fire("on_file_change", count=len(cls._changed_files))

    @classmethod
    def stop(cls):
        if cls._observer and cls._observer.is_alive():
            cls._observer.stop()
            cls._observer.join(timeout=3)
            cls._observer = None
            return "File watcher stopped"
        return "File watcher not running"

    @classmethod
    def status(cls) -> dict:
        running = cls._observer is not None and cls._observer.is_alive()
        return {"running": running, "pending_changes": len(cls._changed_files)}

# ============== CHECKPOINT SYSTEM ==============
class CheckpointManager:
    """Git-based checkpoint/rollback system."""

    @staticmethod
    def create_checkpoint(label: str = "") -> str:
        """Create a git stash checkpoint."""
        try:
            # Stage everything
            subprocess.run(["git", "add", "-A"], capture_output=True, text=True)
            # Check if there's anything to stash
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
            if not status.stdout.strip():
                return "Nothing to checkpoint (clean working tree)"
            # Create stash with label
            tag = label or f"checkpoint-{int(time.time())}"
            r = subprocess.run(["git", "stash", "push", "-m", tag], capture_output=True, text=True)
            # Save checkpoint metadata
            cp_dir = CHECKPOINTS_DIR / tag
            cp_dir.mkdir(parents=True, exist_ok=True)
            meta = {
                "label": tag,
                "timestamp": datetime.now().isoformat(),
                "stash_output": r.stdout.strip(),
                "files_changed": status.stdout.strip().count("\n") + (1 if status.stdout.strip() else 0),
            }
            (cp_dir / "meta.json").write_text(json.dumps(meta, indent=2))
            # Save diff snapshot
            diff = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True)
            if diff.stdout:
                (cp_dir / "snapshot.diff").write_text(diff.stdout)
            return f"Checkpoint created: {tag} ({meta['files_changed']} files)"
        except Exception as e: return f"Checkpoint error: {e}"

    @staticmethod
    def list_checkpoints() -> List[Dict]:
        """List all checkpoints."""
        checkpoints = []
        for cp_dir in sorted(CHECKPOINTS_DIR.iterdir()):
            if cp_dir.is_dir():
                meta_file = cp_dir / "meta.json"
                if meta_file.exists():
                    meta = json.loads(meta_file.read_text())
                    checkpoints.append(meta)
        return checkpoints

    @staticmethod
    def restore_checkpoint(label: str) -> str:
        """Restore a checkpoint by popping the stash."""
        try:
            r = subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
            if r.returncode == 0:
                return f"Restored checkpoint: {label}\n{r.stdout.strip()}"
            else:
                return f"Restore failed: {r.stderr.strip()}"
        except Exception as e: return f"Restore error: {e}"

    @staticmethod
    def delete_checkpoint(label: str) -> str:
        """Delete checkpoint metadata."""
        cp_dir = CHECKPOINTS_DIR / label
        if cp_dir.exists():
            shutil.rmtree(cp_dir)
            return f"Deleted checkpoint: {label}"
        return f"Checkpoint not found: {label}"

# ============== CODEBASE INDEXER ==============
class CodebaseIndexer:
    """Lightweight codebase indexing for semantic search. Uses file hashing + keyword matching."""

    def __init__(self):
        self.index: Dict[str, Dict] = {}  # filepath -> {hash, keywords, size, ext}
        self.index_path = CHECKPOINTS_DIR / "codebase_index.json"
        self._load_index()

    def _load_index(self):
        if self.index_path.exists():
            try:
                self.index = json.loads(self.index_path.read_text())
            except: self.index = {}

    def _save_index(self):
        self.index_path.write_text(json.dumps(self.index, indent=2))

    def _extract_keywords(self, content: str, ext: str) -> List[str]:
        """Extract meaningful keywords from source code."""
        keywords = set()
        # Remove strings and comments for cleaner extraction
        content_lower = content.lower()
        # Extract function/class/variable names
        patterns = [
            r'def\s+(\w+)', r'class\s+(\w+)', r'function\s+(\w+)',
            r'const\s+(\w+)', r'let\s+(\w+)', r'var\s+(\w+)',
            r'import\s+.*from\s+["\']([^"\']+)', r'require\s*\(\s*["\']([^"\']+)',
        ]
        for pat in patterns:
            for match in re.finditer(pat, content):
                keywords.add(match.group(1).lower())
        # Add common programming terms
        for word in re.findall(r'\b[a-z_]\w{3,}\b', content_lower):
            if word not in ('self', 'this', 'that', 'with', 'from', 'import', 'return', 'true', 'false', 'none'):
                keywords.add(word)
        return list(keywords)[:100]  # Cap at 100 keywords

    def index_project(self, root_path: str = ".") -> str:
        """Index all source files in the project."""
        root = Path(root_path).resolve()
        indexed = 0
        skipped = 0
        for f in root.rglob("*"):
            if f.is_file() and f.suffix in ('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.rb', '.php', '.c', '.cpp', '.h', '.md', '.json', '.yaml', '.yml', '.toml'):
                if '.git' in f.parts or 'node_modules' in f.parts or '__pycache__' in f.parts:
                    continue
                try:
                    rel = str(f.relative_to(root))
                    content = f.read_text(errors='ignore')
                    file_hash = hashlib.md5(content.encode()).hexdigest()
                    # Check if changed
                    if rel in self.index and self.index[rel].get('hash') == file_hash:
                        skipped += 1
                        continue
                    keywords = self._extract_keywords(content, f.suffix)
                    self.index[rel] = {
                        'hash': file_hash,
                        'keywords': keywords,
                        'size': f.stat().st_size,
                        'ext': f.suffix,
                        'indexed_at': datetime.now().isoformat(),
                    }
                    indexed += 1
                except: pass
        self._save_index()
        return f"Indexed {indexed} files, {skipped} unchanged. Total: {len(self.index)}"

    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        """Search the index by keyword matching."""
        query_lower = query.lower()
        query_words = set(re.findall(r'\b\w+\b', query_lower))
        results = []
        for filepath, meta in self.index.items():
            score = 0
            keywords = set(meta.get('keywords', []))
            # Exact filename match
            if query_lower in filepath.lower():
                score += 10
            # Keyword overlap
            overlap = query_words & keywords
            score += len(overlap) * 2
            # Partial matches
            for qw in query_words:
                for kw in keywords:
                    if qw in kw or kw in qw:
                        score += 1
            if score > 0:
                results.append({"path": filepath, "score": score, "ext": meta.get('ext', ''), "size": meta.get('size', 0)})
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:max_results]

    def get_stats(self) -> Dict:
        """Get index statistics."""
        exts = {}
        for meta in self.index.values():
            ext = meta.get('ext', 'unknown')
            exts[ext] = exts.get(ext, 0) + 1
        return {"total_files": len(self.index), "by_extension": exts}

# ============== SAFETY MANAGER ==============
class SafetyManager:
    """Plan/Act gate for autonomous operations."""

    def __init__(self, mode: str = "suggest"):
        self.mode = mode  # suggest, plan, auto
        self._pending_approvals: Dict[str, Dict] = {}

    def should_approve(self, tool_name: str, args: dict) -> Tuple[bool, str]:
        """Check if tool execution needs user approval."""
        if self.mode == "auto":
            return True, "auto-mode"

        # Always allow read-only tools
        READ_ONLY = {"read_file", "list_files", "grep", "git_status", "git_log",
                      "git_diff_preview", "map_repo", "get_time", "lsp_diagnostics",
                      "web_search", "web_fetch", "test_provider", "list_providers",
                      "explain_code", "review_code", "refactor_code"}
        if tool_name in READ_ONLY:
            return True, "read-only"

        # Destructive tools need approval in suggest/plan mode
        DESTRUCTIVE = {"write_file", "edit_file_line", "append_file", "run_command",
                       "docker_execute", "git_commit", "git_push", "git_undo"}
        if tool_name in DESTRUCTIVE and self.mode in ("suggest", "plan"):
            approval_id = f"appr-{int(time.time()*1000)}"
            self._pending_approvals[approval_id] = {
                "tool": tool_name, "args": args,
                "timestamp": datetime.now().isoformat(), "status": "pending"
            }
            return False, approval_id

        return True, "allowed"

    def approve(self, approval_id: str) -> bool:
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["status"] = "approved"
            return True
        return False

    def deny(self, approval_id: str) -> bool:
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["status"] = "denied"
            del self._pending_approvals[approval_id]
            return True
        return False

    def get_pending(self) -> List[Dict]:
        return [{"id": k, **v} for k, v in self._pending_approvals.items() if v["status"] == "pending"]

# ============== PROVIDERS ==============
PROVIDER_CONFIGS = {
    "openai":     {"env": "OPENAI_API_KEY",       "lib": "openai",     "client": "OpenAI",     "default_model": "gpt-4o-mini"},
    "anthropic":  {"env": "ANTHROPIC_API_KEY",    "lib": "anthropic",  "client": "Anthropic",  "default_model": "claude-3-5-sonnet-20241022"},
    "openrouter": {"env": "OPENROUTER_API_KEY",   "lib": "openai",     "base": "https://openrouter.ai/api/v1", "default_model": "openai/gpt-4o-mini"},
    "ollama":     {"env": "",                     "lib": "openai",     "base": os.getenv("OLLAMA_HOST","http://localhost:11434")+"/v1", "default_model": "qwen2.5-coder:7b"},
    "hermes":     {"env": "",                     "lib": "openai",     "base": os.getenv("OLLAMA_HOST","http://localhost:11434")+"/v1", "default_model": "hermes3:8b", "description": "Nous Hermes 3 via Ollama — uncensored, tool-use, reasoning"},
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
    "bedrock":    {"env": "AWS_ACCESS_KEY_ID",    "lib": "openai",     "base": None, "default_model": "anthropic.claude-3-5-sonnet-20241022-v2:0"},
    "azure":      {"env": "AZURE_OPENAI_API_KEY", "lib": "openai",     "base": None, "default_model": "gpt-4o-mini"},
    "huggingface":{"env": "HF_TOKEN",             "lib": "openai",     "base": "https://api-inference.huggingface.co/v1", "default_model": "meta-llama/Llama-3.3-70B-Instruct"},
    "replicate":  {"env": "REPLICATE_API_TOKEN",  "lib": "openai",     "base": "https://api.replicate.com/v1", "default_model": "meta/meta-llama-3.1-70b-instruct"},
}

def _get_provider_client(provider: str = None):
    provider = provider or LLM_PROVIDER
    cfg = PROVIDER_CONFIGS.get(provider)
    if not cfg: raise ValueError(f"Unknown provider: {provider}")
    lib = cfg.get("lib")
    mod = importlib.import_module(lib)
    client_class_name = cfg.get("client", "OpenAI")
    ClientClass = getattr(mod, client_class_name)
    api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
    base = cfg.get("base")
    if base and not api_key: api_key = "ollama"  # Ollama doesn't need a real key
    if base: return ClientClass(api_key=api_key, base_url=base)
    return ClientClass(api_key=api_key) if api_key else ClientClass()

class ProviderRouter:
    @staticmethod
    def call(messages: List[Dict], tools_schemas: List[Dict], provider: str = None):
        """Non-streaming call. Returns response object with .content and .tool_calls."""
        provider = provider or LLM_PROVIDER
        cfg = PROVIDER_CONFIGS.get(provider)
        if not cfg: raise ValueError(f"Unsupported provider: {provider}")
        model = os.getenv("MODEL_NAME", cfg.get("default_model", "gpt-4o-mini"))
        api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
        base = cfg.get("base")

        if cfg.get("lib") == "openai":
            import openai
            if base and not api_key: api_key = "ollama"  # Ollama doesn't need a real key
            client = openai.OpenAI(api_key=api_key, base_url=base) if api_key or base else openai.OpenAI()
            om = []
            for m in messages:
                if m["role"] == "system": om.append({"role": "system", "content": m["content"]})
                elif m["role"] == "user": om.append({"role": "user", "content": m.get("content","")})
                elif m["role"] == "assistant": om.append({"role": "assistant", "content": m.get("content","")})
                elif m["role"] == "tool": om.append({"role": "tool", "tool_call_id": m.get("tool_call_id",""), "content": m["content"]})
            resp = client.chat.completions.create(model=model, messages=om, tools=tools_schemas if tools_schemas else None, tool_choice="auto" if tools_schemas else None)
            _track_cost(provider, resp.usage.prompt_tokens, resp.usage.completion_tokens)
            return resp.choices[0].message

        elif cfg.get("lib") == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            cm = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ["user","assistant"]]
            an_tools = [{"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tools_schemas]
            resp = client.messages.create(model=model, system=system, messages=cm, tools=an_tools, max_tokens=4096)
            _track_cost(provider, resp.usage.input_tokens, resp.usage.output_tokens)
            # Convert to OpenAI-like response
            content_text = ""
            tool_calls = []
            for block in resp.content:
                if hasattr(block, "type") and block.type == "text":
                    content_text += block.text
                elif hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append(type('ToolCall', (), {
                        'id': block.id,
                        'function': type('Function', (), {
                            'name': block.name,
                            'arguments': json.dumps(block.input)
                        })()
                    })())
            result = type('Response', (), {'content': content_text, 'tool_calls': tool_calls})()
            return result

        elif cfg.get("lib") == "google.generativeai":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            gen_model = genai.GenerativeModel(model)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            history = []
            for m in messages:
                if m["role"] == "system": continue
                history.append({"role": "model" if m["role"] == "assistant" else "user", "parts": [m.get("content", "")]})
            chat = gen_model.start_chat(history=history[:-1] if history else [])
            last_msg = history[-1]["parts"][0] if history else "Hello"
            resp = chat.send_message(last_msg)
            text = resp.text if hasattr(resp, "text") else ""
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            if hasattr(resp, "usage_metadata"):
                usage = {
                    "prompt_tokens": getattr(resp.usage_metadata, "prompt_token_count", 0),
                    "completion_tokens": getattr(resp.usage_metadata, "candidates_token_count", 0),
                }
            _track_cost(provider, usage["prompt_tokens"], usage["completion_tokens"])
            return type('Response', (), {'content': text, 'tool_calls': []})()

        else:
            # Generic OpenAI-compatible fallback
            try:
                import openai
                if base and not api_key: api_key = "ollama"
                client = openai.OpenAI(api_key=api_key, base_url=base) if api_key or base else openai.OpenAI()
                om = [{"role": m["role"], "content": m.get("content","")} for m in messages if m["role"] in ("system","user","assistant")]
                resp = client.chat.completions.create(model=model, messages=om, tools=tools_schemas if tools_schemas else None, tool_choice="auto" if tools_schemas else None)
                _track_cost(provider, resp.usage.prompt_tokens, resp.usage.completion_tokens)
                return resp.choices[0].message
            except Exception as e:
                raise ValueError(f"Provider {provider} failed: {e}")

    @staticmethod
    def call_stream(messages: List[Dict], tools_schemas: List[Dict], provider: str = None):
        """Streaming variant: yields (chunk_text, tool_calls_or_None, usage_dict)."""
        provider = provider or LLM_PROVIDER
        cfg = PROVIDER_CONFIGS.get(provider)
        if not cfg: raise ValueError(f"Unsupported provider: {provider}")
        model = os.getenv("MODEL_NAME", cfg.get("default_model", "gpt-4o-mini"))
        api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
        base = cfg.get("base")

        if cfg.get("lib") == "openai":
            import openai
            if base and not api_key: api_key = "ollama"  # Ollama doesn't need a real key
            client = openai.OpenAI(api_key=api_key, base_url=base) if api_key or base else openai.OpenAI()
            om = []
            for m in messages:
                if m["role"] == "system": om.append({"role": "system", "content": m["content"]})
                elif m["role"] == "user": om.append({"role": "user", "content": m.get("content","")})
                elif m["role"] == "assistant": om.append({"role": "assistant", "content": m.get("content","")})
                elif m["role"] == "tool": om.append({"role": "tool", "tool_call_id": m.get("tool_call_id",""), "content": m["content"]})
            stream = client.chat.completions.create(model=model, messages=om, tools=tools_schemas if tools_schemas else None, tool_choice="auto" if tools_schemas else None, stream=True)
            tool_calls = []
            content_parts = []
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta:
                    if delta.content:
                        content_parts.append(delta.content)
                        yield delta.content, None, None
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            while len(tool_calls) <= idx:
                                tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                            if tc.id: tool_calls[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls[idx]["function"]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls[idx]["function"]["arguments"] += tc.function.arguments
                if hasattr(chunk, 'usage') and chunk.usage:
                    usage = {"prompt_tokens": chunk.usage.prompt_tokens, "completion_tokens": chunk.usage.completion_tokens}
            _track_cost(provider, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            class StreamResult:
                def __init__(self):
                    self.content = "".join(content_parts) or None
                    self.tool_calls = tool_calls if tool_calls and tool_calls[0]["id"] else []
                    self.usage = type('U', (), usage)()
            yield None, StreamResult(), usage

        elif cfg.get("lib") == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            cm = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ["user","assistant"]]
            an_tools = [{"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tools_schemas]
            content_parts = []
            tool_calls = []
            with client.messages.stream(model=model, system=system, messages=cm, tools=an_tools, max_tokens=4096) as stream:
                for text in stream.text_stream:
                    content_parts.append(text)
                    yield text, None, None
                resp = stream.get_final_message()
            for block in resp.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append({"id": block.id, "function": {"name": block.name, "arguments": json.dumps(block.input)}})
            _track_cost(provider, resp.usage.input_tokens, resp.usage.output_tokens)
            class StreamResult:
                def __init__(self):
                    self.content = "".join(content_parts) or None
                    self.tool_calls = tool_calls
                    self.usage = type('U', (), {"prompt_tokens": resp.usage.input_tokens, "completion_tokens": resp.usage.output_tokens})()
            yield None, StreamResult(), {}

        elif cfg.get("lib") == "google.generativeai":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            gen_model = genai.GenerativeModel(model)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            history = []
            for m in messages:
                if m["role"] == "system": continue
                history.append({"role": "model" if m["role"] == "assistant" else "user", "parts": [m.get("content", "")]})
            chat = gen_model.start_chat(history=history[:-1] if history else [])
            last_msg = history[-1]["parts"][0] if history else "Hello"
            content_parts = []
            tool_calls = []
            for chunk in chat.send_message_streaming(last_msg):
                if hasattr(chunk, "text") and chunk.text:
                    content_parts.append(chunk.text)
                    yield chunk.text, None, None
            resp = chat.last if hasattr(chat, "last") else None
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            if resp and hasattr(resp, "usage_metadata"):
                usage = {
                    "prompt_tokens": getattr(resp.usage_metadata, "prompt_token_count", 0),
                    "completion_tokens": getattr(resp.usage_metadata, "candidates_token_count", 0),
                }
            _track_cost(provider, usage["prompt_tokens"], usage["completion_tokens"])
            class StreamResult:
                def __init__(self):
                    self.content = "".join(content_parts) or None
                    self.tool_calls = tool_calls
                    self.usage = type('U', (), usage)()
            yield None, StreamResult(), usage

        else:
            # Generic fallback: use non-streaming call
            result = ProviderRouter.call(messages, tools_schemas, provider)
            yield None, result, {}


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
                    print(f"Zombie worker: {w.name}")
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
def compress_messages(messages: List[Dict], keep_recent: int = 5, provider: str = "") -> List[Dict]:
    if len(messages) <= 20: return messages
    system_msg = messages.pop(0) if messages and messages[0]["role"] == "system" else None
    middle = messages[3:-keep_recent]; recent = messages[-keep_recent:]
    if not middle: return messages
    prompt = "Summarize this conversation concisely:\n" + "\n".join([f"{m['role']}: {str(m.get('content',''))[:200]}" for m in middle])
    try:
        p = provider or LLM_PROVIDER
        cfg = PROVIDER_CONFIGS.get(p, {})
        client = _get_provider_client(p)
        model = cfg.get("default_model", "gpt-4o-mini")
        resp = client.chat.completions.create(model=model, messages=[{"role":"user","content":prompt}], max_tokens=200)
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
                return f"Installed plugin '{data.get('name', target_name)}' v{data.get('version', '0')}"
            return f"Extracted to {target_dir} but no plugin.json found"
        except Exception as e:
            return f"Install failed: {e}"

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
        try:
            origin = websocket.request_headers.get("Origin", "")
            cors_origin = os.getenv("CORS_ORIGIN", "tauri://localhost")
            if origin and cors_origin and origin != cors_origin and origin != "null":
                pass
        except:
            pass
        self.clients.add(websocket)
        try:
            async for message in websocket:
                data = json.loads(message)
                msg_type = data.get("type", "chat")
                if msg_type == "chat":
                    # Safety check
                    approved, reason = self.agent.safety.should_approve("chat", data)
                    if not approved:
                        await websocket.send(json.dumps({"type":"approval_needed","id":reason,"tool":"chat","args":data}))
                        continue
                    # Streaming chat
                    self.agent.messages.append({"role":"user","content":data["text"]})
                    HookManager.fire("pre_llm", messages=self.agent.messages)
                    schemas = self.agent.registry.get_openai_schemas()
                    content_parts = []
                    tool_calls = []
                    for chunk_text, result, usage in ProviderRouter.call_stream(self.agent.messages, schemas, self.agent.provider):
                        if chunk_text:
                            content_parts.append(chunk_text)
                            await websocket.send(json.dumps({"type":"token", "content":chunk_text}))
                        if result:
                            content_parts = [result.content or ""]
                            tool_calls = result.tool_calls
                    HookManager.fire("post_llm", content="".join(content_parts), tool_calls=tool_calls)
                    full_content = "".join(content_parts)
                    self.agent.messages.append({"role":"assistant","content":full_content})
                    if tool_calls:
                        calls = []
                        for tc in tool_calls:
                            f = tc.get("function", tc) if isinstance(tc, dict) else getattr(tc, "function", tc)
                            calls.append({"id": tc.get("id","") if isinstance(tc,dict) else getattr(tc,"id",""), "name": f.get("name","") if isinstance(f,dict) else getattr(f,"name",""), "args": json.loads(f.get("arguments","{}") if isinstance(f,dict) else getattr(f,"arguments","{}"))})
                        # Safety check for each tool call
                        approved_calls = []
                        for call in calls:
                            ok, reason = self.agent.safety.should_approve(call["name"], call["args"])
                            if ok:
                                approved_calls.append(call)
                            else:
                                await websocket.send(json.dumps({"type":"approval_needed","id":reason,"tool":call["name"],"args":call["args"]}))
                        if approved_calls:
                            HookManager.fire("pre_tool", calls=approved_calls)
                            results = self.agent.registry.execute_parallel(approved_calls)
                            HookManager.fire("post_tool", results=results)
                            for res in results:
                                self.agent.messages.append({"role":"tool","tool_call_id":res["id"],"content":res["result"]})
                            # Auto-heal on tool errors
                            for res in results:
                                if "Error" in res["result"] or "ToolError" in res["result"]:
                                    for c in approved_calls:
                                        if c["id"] == res["id"]:
                                            fix = SelfHealer.auto_fix(c["name"], res["result"], c["args"])
                                            if fix:
                                                self.agent.logs.append({"type":"auto_fix", "tool": c["name"], "fix": fix})
                            # Run another LLM iteration with tool results
                            for chunk_text, result2, _ in ProviderRouter.call_stream(self.agent.messages, schemas, self.agent.provider):
                                if chunk_text:
                                    await websocket.send(json.dumps({"type":"token", "content":chunk_text}))
                                if result2:
                                    full_content = result2.content or ""
                            self.agent.messages.append({"role":"assistant","content":full_content})
                    self.agent.store.save(self.agent.session_id, self.agent.messages)
                    await websocket.send(json.dumps({"type":"response","content":full_content,"toolCalls":[{"name":c["name"],"args":c["args"]} for c in tool_calls] if tool_calls else []}))
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
                    elif cmd == "undo":
                        result = git_undo()
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                        await websocket.send(json.dumps({"type":"diff", "data":_git_diff()}))
                    elif cmd == "sessions":
                        sessions = self.agent.store.list_sessions()
                        await websocket.send(json.dumps({"type":"sessions", "data":sessions}))
                    elif cmd.startswith("session:load:"):
                        sid = cmd.split(":", 2)[2]
                        msgs = self.agent.store.load(sid)
                        if msgs:
                            self.agent.messages = msgs
                            await websocket.send(json.dumps({"type":"notification", "content":f"Loaded session {sid}"}))
                        else:
                            await websocket.send(json.dumps({"type":"notification", "content":f"Session {sid} not found"}))
                    elif cmd == "session:save":
                        self.agent.store.save(self.agent.session_id, self.agent.messages)
                        await websocket.send(json.dumps({"type":"notification", "content":f"Session saved: {self.agent.session_id}"}))
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
                    elif cmd == "system_prompt":
                        await websocket.send(json.dumps({"type":"system_prompt", "data":self.agent.system_prompt}))
                    elif cmd.startswith("system_prompt:set:"):
                        new_prompt = cmd.split(":", 2)[2]
                        self.agent.system_prompt = new_prompt
                        await websocket.send(json.dumps({"type":"notification", "content":"System prompt updated"}))
                    elif cmd == "files":
                        result = registry.execute("list_files", {"path": ".", "max_depth": "3"})
                        await websocket.send(json.dumps({"type":"files", "data":json.loads(result)}))
                    elif cmd.startswith("files:"):
                        target = cmd.split(":", 1)[1]
                        result = registry.execute("list_files", {"path": target, "max_depth": "3"})
                        await websocket.send(json.dumps({"type":"files", "data":json.loads(result)}))
                    elif cmd == "git_branches":
                        result = registry.execute("git_branch", {})
                        await websocket.send(json.dumps({"type":"git_branches", "data":result}))
                    elif cmd == "git_log":
                        result = registry.execute("git_log", {"n": "20"})
                        await websocket.send(json.dumps({"type":"git_log", "data":result}))
                    elif cmd.startswith("approve:"):
                        wid = cmd.split(":", 1)[1]
                        if self.agent.safety.approve(wid):
                            await websocket.send(json.dumps({"type":"notification", "content":"Approved"}))
                        else:
                            result = registry.execute("confirm_write", {"write_id": wid})
                            await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("deny:"):
                        wid = cmd.split(":", 1)[1]
                        if self.agent.safety.deny(wid):
                            await websocket.send(json.dumps({"type":"notification", "content":"Denied"}))
                        else:
                            result = registry.execute("deny_write", {"write_id": wid})
                            await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd == "hooks":
                        await websocket.send(json.dumps({"type":"hooks", "data":HookManager.list_hooks()}))
                    elif cmd.startswith("hook:register:"):
                        parts = cmd.split(":", 2)
                        event = parts[1]
                        HookManager.register(event, lambda: None)
                        await websocket.send(json.dumps({"type":"notification", "content":f"Registered hook for {event}"}))
                    elif cmd == "checkpoints":
                        cps = CheckpointManager.list_checkpoints()
                        transformed = []
                        for cp in cps:
                            transformed.append({
                                "id": cp.get("label", ""),
                                "label": cp.get("label", ""),
                                "timestamp": cp.get("timestamp", ""),
                                "filesChanged": cp.get("files_changed", 0),
                            })
                        await websocket.send(json.dumps({"type":"checkpoints", "data":transformed}))
                    elif cmd.startswith("checkpoint:create:"):
                        label = cmd.split(":", 2)[2] if len(cmd.split(":")) > 2 else ""
                        result = CheckpointManager.create_checkpoint(label)
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("checkpoint:restore:"):
                        label = cmd.split(":", 2)[2]
                        result = CheckpointManager.restore_checkpoint(label)
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("checkpoint:delete:"):
                        label = cmd.split(":", 2)[2]
                        result = CheckpointManager.delete_checkpoint(label)
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd == "index":
                        result = self.agent.indexer.index_project()
                        await websocket.send(json.dumps({"type":"notification", "content":result}))
                    elif cmd.startswith("search:"):
                        query = cmd.split(":", 1)[1]
                        results = self.agent.indexer.search(query)
                        await websocket.send(json.dumps({"type":"search_results", "data":results}))
                    elif cmd == "index:stats":
                        stats = self.agent.indexer.get_stats()
                        await websocket.send(json.dumps({"type":"index_stats", "data":{
                            "totalFiles": stats.get("total_files", 0),
                            "totalChunks": sum(stats.get("by_extension", {}).values()),
                            "lastUpdated": None,
                        }}))
                    elif cmd.startswith("safety:mode:"):
                        mode = cmd.split(":", 2)[2]
                        self.agent.safety.mode = mode
                        await websocket.send(json.dumps({"type":"safety_mode", "data":mode}))
                    elif cmd == "safety:pending":
                        pending = self.agent.safety.get_pending()
                        transformed = []
                        for p in pending:
                            transformed.append({
                                "id": p.get("id", ""),
                                "tool": p.get("tool", ""),
                                "args": p.get("args", {}),
                                "timestamp": p.get("timestamp", ""),
                            })
                        await websocket.send(json.dumps({"type":"pending_approvals","data":transformed}))
                    elif cmd == "safety:status":
                        await websocket.send(json.dumps({"type":"safety_mode","data":self.agent.safety.mode}))
                    elif cmd.startswith("suggest:confirm:"):
                        wid = cmd.split(":", 2)[2]
                        result = registry.execute("confirm_write", {"write_id": wid})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("suggest:deny:"):
                        wid = cmd.split(":", 2)[2]
                        result = registry.execute("deny_write", {"write_id": wid})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd == "index:reindex":
                        result = self.agent.indexer.index_project()
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("index:search:"):
                        query = cmd.split(":", 2)[2]
                        results = self.agent.indexer.search(query)
                        await websocket.send(json.dumps({"type":"index_results","data":results}))
                    elif cmd.startswith("model:"):
                        model = cmd.split(":", 1)[1]
                        os.environ["MODEL_NAME"] = model
                        await websocket.send(json.dumps({"type":"model", "data":model}))
                    elif cmd == "watcher:start":
                        result = FileWatcher.start(self.agent.indexer)
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd == "watcher:stop":
                        result = FileWatcher.stop()
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd == "watcher:status":
                        await websocket.send(json.dumps({"type":"watcher_status","data":FileWatcher.status()}))
                    elif cmd.startswith("grep:"):
                        parts = cmd.split(":", 3)
                        pattern = parts[1] if len(parts) > 1 else ""
                        path = parts[2] if len(parts) > 2 else "."
                        result = registry.execute("grep", {"pattern": pattern, "path": path})
                        await websocket.send(json.dumps({"type":"grep_results","data":result}))
                    elif cmd.startswith("rename:"):
                        parts = cmd.split(":", 3)
                        old_name = parts[1] if len(parts) > 1 else ""
                        new_name = parts[2] if len(parts) > 2 else ""
                        result = registry.execute("rename_symbol", {"old_name": old_name, "new_name": new_name})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("explain:"):
                        code = cmd.split(":", 1)[1]
                        result = registry.execute("explain_code", {"code": code})
                        await websocket.send(json.dumps({"type":"explain","data":result}))
                    elif cmd.startswith("review:"):
                        code = cmd.split(":", 1)[1]
                        result = registry.execute("review_code", {"code": code})
                        await websocket.send(json.dumps({"type":"review","data":result}))
                    elif cmd.startswith("refactor:"):
                        code = cmd.split(":", 1)[1]
                        result = registry.execute("refactor_code", {"code": code})
                        await websocket.send(json.dumps({"type":"refactor","data":result}))
                    elif cmd.startswith("bg:start:"):
                        command = cmd.split(":", 2)[2]
                        result = registry.execute("background_process", {"command": command})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("bg:poll:"):
                        pid = cmd.split(":", 2)[2]
                        result = registry.execute("poll_process", {"pid": pid})
                        await websocket.send(json.dumps({"type":"bg_output","data":result}))
                    elif cmd.startswith("bg:kill:"):
                        pid = cmd.split(":", 2)[2]
                        result = registry.execute("kill_process", {"pid": pid})
                        await websocket.send(json.dumps({"type":"notification","content":result}))
                    elif cmd.startswith("lint:"):
                        path_arg = cmd.split(":", 1)[1] if ":" in cmd else "."
                        result = registry.execute("lint_and_test", {"path": path_arg})
                        await websocket.send(json.dumps({"type":"lint_results","data":result}))
                    elif cmd.startswith("task:"):
                        parts = cmd.split(":", 2)
                        action = parts[1] if len(parts) > 1 else "list"
                        arg = parts[2] if len(parts) > 2 else ""
                        result = registry.execute("task_board", {"action": action, "task_name": arg})
                        await websocket.send(json.dumps({"type":"task_board","data":result}))
                    elif cmd.startswith("repo:map"):
                        path_arg = cmd.split(":", 2)[2] if cmd.count(":") >= 2 else "."
                        result = registry.execute("repo_map", {"path": path_arg})
                        await websocket.send(json.dumps({"type":"repo_map","data":result}))
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
            print(f"WebSocket server on ws://{WS_HOST}:{WS_PORT}")
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
        self.safety = SafetyManager(SAFETY_MODE)
        self.checkpoints = CheckpointManager()
        self.indexer = CodebaseIndexer()
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
        @self.registry.register(description="Task board: list, add, move, remove tasks")
        def task_board(action: str = "list", task_name: str = "", column: str = "") -> str:
            if action == "list":
                return json.dumps(self.kanban.get_board_state(), indent=1)
            elif action == "add" and task_name:
                task = self.kanban.add_task(task_name)
                return f"Added task: {task.id} - {task.title}"
            elif action == "move" and task_name:
                self.kanban.move_task(task_name, column or "done")
                return f"Moved task {task_name} to {column or 'done'}"
            elif action == "remove" and task_name:
                self.kanban.remove_task(task_name)
                return f"Removed task: {task_name}"
            return f"Unknown task action: {action}"
        @self.registry.register(description="Create a checkpoint of current changes")
        def create_checkpoint(label: str = "") -> str: return self.checkpoints.create_checkpoint(label)
        @self.registry.register(description="List all checkpoints")
        def list_checkpoints() -> str: return json.dumps(self.checkpoints.list_checkpoints(), indent=2)
        @self.registry.register(description="Restore a checkpoint by label")
        def restore_checkpoint(label: str) -> str: return self.checkpoints.restore_checkpoint(label)
        @self.registry.register(description="Index the codebase for semantic search")
        def index_codebase(path: str = ".") -> str: return self.indexer.index_project(path)
        @self.registry.register(description="Search the codebase index")
        def search_index(query: str) -> str: return json.dumps(self.indexer.search(query), indent=2)
        @self.registry.register(description="Analyze an error and suggest fixes")
        def analyze_error(error: str, context: str = "") -> str: return SelfHealer.analyze_error(error, context)
        @self.registry.register(description="Set safety mode: suggest, plan, or auto")
        def set_safety_mode(mode: str) -> str:
            self.safety.mode = mode
            return f"Safety mode set to: {mode}"
    def run_loop(self, messages: List[Dict] = None, max_iters: int = 10) -> str:
        if messages is None: messages = [{"role": "system", "content": self.system_prompt}]
        self.messages = messages.copy()
        if self.messages and len(self.messages) >= 2:
            user_content = self.messages[-1].get("content", "") if self.messages[-1]["role"] == "user" else ""
            if len(user_content) > 20 and _check_prompt_injection(user_content):
                self.logs.append({"type": "security_warning", "message": "Possible prompt injection detected"})
                return "Blocked: possible prompt injection detected."
        self.logs.append({"type": "run_start", "timestamp": time.time(), "iterations": 0})
        HookManager.fire("on_start")
        for i in range(max_iters):
            self.logs.append({"type": "llm_call", "iteration": i, "timestamp": time.time()})
            HookManager.fire("pre_llm", messages=self.messages)
            schemas = self.registry.get_openai_schemas()
            try: response = ProviderRouter.call(self.messages, schemas, self.provider)
            except Exception as e:
                HookManager.fire("on_error", error=str(e))
                return f"LLM Error: {e}"
            self.messages.append({"role": "assistant", "content": response.content or ""})
            HookManager.fire("post_llm", content=response.content, tool_calls=getattr(response, "tool_calls", []))
            tcs = getattr(response, "tool_calls", [])
            if not tcs:
                self.logs.append({"type": "llm_response", "iteration": i, "content": (response.content or "")[:200]})
                HookManager.fire("on_stop")
                return response.content or "No response"
            calls = []
            for tc in tcs:
                f = tc.function if hasattr(tc, "function") else tc["function"]
                calls.append({"id": tc.id if hasattr(tc,"id") else tc["id"], "name": f.name if hasattr(f,"name") else f["name"], "args": json.loads(f.arguments if hasattr(f,"arguments") else f["arguments"])})
            # Safety check
            approved_calls = []
            for call in calls:
                ok, reason = self.safety.should_approve(call["name"], call["args"])
                if ok:
                    approved_calls.append(call)
                else:
                    self.logs.append({"type": "approval_needed", "id": reason, "tool": call["name"]})
            if not approved_calls:
                return "All tool calls require approval. Use suggest mode or approve via WS."
            HookManager.fire("pre_tool", calls=approved_calls)
            results = self.registry.execute_parallel(approved_calls)
            HookManager.fire("post_tool", results=results)
            for c in approved_calls:
                self.logs.append({"type": "tool_call", "name": c["name"], "args": c["args"], "iteration": i, "timestamp": time.time()})
            for res in results:
                self.logs.append({"type": "tool_result", "id": res["id"], "result": res["result"][:200], "iteration": i, "timestamp": time.time()})
                # Auto-heal on errors
                if "Error" in res["result"] or "ToolError" in res["result"]:
                    for c in approved_calls:
                        if c["id"] == res["id"]:
                            fix = SelfHealer.auto_fix(c["name"], res["result"], c["args"])
                            if fix:
                                self.logs.append({"type": "auto_fix", "tool": c["name"], "fix": fix})
            if SelfLearner._recording:
                for res in results:
                    for c in approved_calls:
                        if c["id"] == res["id"]: SelfLearner.record_action(c["name"], c["args"]); break
            for res in results:
                self.messages.append({"role": "tool", "tool_call_id": res["id"], "content": res["result"]})
            if self.goal_manager and self.goal_manager.is_complete(response.content or ""):
                HookManager.fire("on_stop")
                return f"Goal Complete: {self.goal_manager.goal}"
        HookManager.fire("on_stop")
        return "Max iterations reached."
    def chat(self):
        import uuid
        sid = self.session_id
        print(f"Hermes-Ultimate | Session: {sid} | Provider: {self.provider} ({MODEL_NAME})")
        print(f"Available providers: {', '.join(PROVIDER_CONFIGS.keys())}")
        print(f"Safety mode: {self.safety.mode}")
        print("Commands: /goal, /multitask, /kanban, /browser, /desktop, /record, /compose, /provider, /checkpoint, /index, /safety, exit\n")
        while True:
            try: u = input("You: ").strip()
            except (EOFError, KeyboardInterrupt): break
            if not u: continue
            if u == "exit": break
            if u == "/reset":
                self.messages = [{"role":"system","content":self.system_prompt}]; print("Reset."); continue
            if u.startswith("/provider"):
                p = u.split(" ", 1)[1] if " " in u else ""
                if p in PROVIDER_CONFIGS: self.provider = p; print(f"Switched to {p}")
                else: print(f"Available: {', '.join(PROVIDER_CONFIGS.keys())}")
                continue
            if u.startswith("/kanban"):
                parts = u.split()
                if len(parts) >= 3 and parts[1]=="add": t=self.kanban.add_task(parts[2]); print(f"{t.id}"); continue
                if len(parts)>=2 and parts[1]=="show": print(json.dumps(self.kanban.get_board_state(), indent=2)); continue
            if u.startswith("/goal"):
                self.goal_manager = GoalManager(u.split(" ",1)[1])
                result = self.run_loop([{"role":"system","content":self.system_prompt},{"role":"user","content":f"Goal: {self.goal_manager.goal}. Work until COMPLETE."}])
                print(f"\n{result}\n"); continue
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
                if len(parts)>=3: SelfLearner.start_recording(parts[1], parts[2]); print(f"Recording '{parts[1]}'"); continue
                else: print("Usage: /record <name> <desc>"); continue
            if u == "/stop_record": print(SelfLearner.stop_recording(self.provider)); continue
            if u.startswith("/compose"):
                rest = u.split(" ",2)
                if len(rest)>=3:
                    names = [s.strip() for s in rest[1].split(",")]
                    print(SelfLearner.compose_workflow(names, rest[2], self.provider)); continue
            if u.startswith("/checkpoint"):
                parts = u.split()
                if len(parts) >= 2 and parts[1] == "create":
                    label = parts[2] if len(parts) > 2 else ""
                    print(self.checkpoints.create_checkpoint(label)); continue
                if len(parts) >= 2 and parts[1] == "list":
                    print(json.dumps(self.checkpoints.list_checkpoints(), indent=2)); continue
                if len(parts) >= 3 and parts[1] == "restore":
                    print(self.checkpoints.restore_checkpoint(parts[2])); continue
            if u.startswith("/index"):
                print(self.indexer.index_project()); continue
            if u.startswith("/search"):
                query = u.split(" ", 1)[1] if " " in u else ""
                print(json.dumps(self.indexer.search(query), indent=2)); continue
            if u.startswith("/safety"):
                parts = u.split()
                if len(parts) >= 2:
                    self.safety.mode = parts[1]
                    print(f"Safety mode: {self.safety.mode}"); continue
                print(f"Current safety mode: {self.safety.mode}"); continue
            result = self.run_loop([{"role":"system","content":self.system_prompt},{"role":"user","content":u}])
            print(f"\n{result}\n")

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
