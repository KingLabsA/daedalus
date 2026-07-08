#!/usr/bin/env python3
import os, sys, json, sqlite3, subprocess, threading, time, asyncio, tempfile, inspect, importlib, io, uuid, socket, hashlib, re
from pathlib import Path
import shutil
from typing import List, Dict, Any, Optional, Callable, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from dotenv import load_dotenv

from core.context import ContextEngine
from core.cognition import EventLog, Dreamer, Distiller, GoalJudge, Subconscious
from core.intel import CodeIntel, SemanticIndex, CausalWorldModel, WorldModelSentinel, LspClient, EmbeddingIndex, HybridSearch
from core.senses import ModelOrchestra, Vision, VoiceIO
from core.senses.orchestra import DEFAULT_PROFILES as _ORCHESTRA_PROFILES
from core.platform import McpClient, DependencyScanner, ProfileBuilder, ModelAdvisor
from core.epistemic import CalibrationTracker, CostAwareRouter, MaxMode
from core.changeset import ChangesetManager, safe_repo_path

# Fable 5 leads the reasoning/creative expert profiles when its key is present
for _profile_name in ("reasoning", "creative", "long_context"):
    if "fable" not in _ORCHESTRA_PROFILES[_profile_name]:
        _ORCHESTRA_PROFILES[_profile_name].insert(0, "fable")

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
# Local models get a pruned toolset — 80+ schemas bury small models and blow up context
CORE_TOOLS = [
    "read_file", "write_file", "append_file", "edit_file_line", "run_command",
    "grep", "list_files", "git_status", "git_diff_preview", "git_commit",
    "semantic_search", "search_index", "remember", "recall_memory", "task_board", "execute_python",
]
# tools that modify the filesystem/system — subject to interactive diff-approval
DESTRUCTIVE_TOOLS = {"write_file", "append_file", "edit_file_line", "run_command", "git_commit", "git_push", "git_undo"}
def _is_destructive(name: str) -> bool:
    return name in DESTRUCTIVE_TOOLS

class _Cancelled(Exception):
    """Raised to abort an in-flight agent run when cancel_event is set."""

_MENTION_RE = re.compile(r"(?<![\w/])@([\w./\-]+\.\w+|[\w./\-]+/[\w./\-]+)")
def _expand_mentions(text: str, max_files: int = 6, max_bytes: int = 60_000) -> str:
    """Expand @path references into attached file contents (like Cursor's @file).
    Only real files under the cwd are attached; the original text is preserved."""
    seen, attachments, budget = set(), [], max_bytes
    for m in _MENTION_RE.finditer(text or ""):
        rel = m.group(1)
        if rel in seen or len(attachments) >= max_files:
            continue
        seen.add(rel)
        p = Path(rel)
        try:
            if not p.is_file():
                continue
            data = p.read_text(errors="replace")
        except OSError:
            continue
        if len(data) > budget:
            data = data[:budget] + "\n[...truncated]"
        budget -= min(len(data), budget)
        lang = p.suffix.lstrip(".") or ""
        attachments.append(f"### {rel}\n```{lang}\n{data}\n```")
        if budget <= 0:
            break
    if not attachments:
        return text
    return text + "\n\n[Attached files referenced with @]\n" + "\n\n".join(attachments)

def _unified_diff(old: str, new: str, path: str) -> str:
    import difflib
    lines = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=2))
    return "\n".join(lines[:200]) if lines else "(no textual change)"

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
        return _mcp_client().call_tool(server, tool, json.loads(arguments or "{}"))
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
        """Snapshot the working tree WITHOUT disturbing it.

        Uses `git stash create` (builds a stash commit object but does not touch
        the working tree or the stash stack) + `git stash store` (records it so
        it's recoverable). This is non-destructive: creating a checkpoint never
        removes the user's uncommitted work — unlike `git stash push`.
        """
        try:
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
            if not status.stdout.strip():
                return "Nothing to checkpoint (clean working tree)"
            tag = label or f"checkpoint-{int(time.time())}"
            created = subprocess.run(["git", "stash", "create", tag], capture_output=True, text=True)
            sha = created.stdout.strip()
            if not sha:
                return "Nothing to checkpoint (no tracked changes)"
            subprocess.run(["git", "stash", "store", "-m", tag, sha], capture_output=True, text=True)
            cp_dir = CHECKPOINTS_DIR / tag
            cp_dir.mkdir(parents=True, exist_ok=True)
            meta = {
                "label": tag,
                "sha": sha,
                "timestamp": datetime.now().isoformat(),
                "files_changed": status.stdout.strip().count("\n") + 1,
            }
            (cp_dir / "meta.json").write_text(json.dumps(meta, indent=2))
            diff = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True)
            if diff.stdout:
                (cp_dir / "snapshot.diff").write_text(diff.stdout)
            return f"Checkpoint created: {tag} ({meta['files_changed']} files, working tree untouched)"
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
        """Apply a checkpoint by its stored SHA (non-destructive: keeps the stash)."""
        try:
            meta_file = CHECKPOINTS_DIR / label / "meta.json"
            sha = None
            if meta_file.exists():
                sha = json.loads(meta_file.read_text()).get("sha")
            cmd = ["git", "stash", "apply", sha] if sha else ["git", "stash", "apply"]
            r = subprocess.run(cmd, capture_output=True, text=True)
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
    "anthropic":  {"env": "ANTHROPIC_API_KEY",    "lib": "anthropic",  "client": "Anthropic",  "default_model": "claude-3-5-sonnet-20241022", "models": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-3-5-sonnet-20241022"]},
    "fable":      {"env": "ANTHROPIC_API_KEY",    "lib": "anthropic",  "client": "Anthropic",  "default_model": "claude-fable-5", "description": "Claude Fable 5 — Anthropic's Mythos-class frontier model", "models": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"]},
    "openrouter": {"env": "OPENROUTER_API_KEY",   "lib": "openai",     "base": "https://openrouter.ai/api/v1", "default_model": "openai/gpt-4o-mini"},
    "ollama":     {"env": "",                     "lib": "openai",     "base": os.getenv("OLLAMA_HOST","http://localhost:11434")+"/v1", "default_model": os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b"), "local": True, "ollama": True},
    "hermes":     {"env": "",                     "lib": "openai",     "base": os.getenv("OLLAMA_HOST","http://localhost:11434")+"/v1", "default_model": "hermes3:8b", "description": "Nous Hermes 3 via Ollama — uncensored, tool-use, reasoning", "models": ["hermes3:3b", "hermes3:8b", "hermes3:70b", "hermes3:405b"], "local": True, "ollama": True},
    "freellmapi": {"env": "FREELLMAPI_API_KEY",   "lib": "openai",     "base": os.getenv("FREELLMAPI_HOST","http://localhost:3002")+"/v1", "default_model": os.getenv("FREELLMAPI_MODEL", "auto"), "description": "Local FreeLLMAPI gateway (67 free models) — launch it first", "local": True},
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

def _model_for(provider: str, cfg: dict) -> str:
    """MODEL_NAME env only overrides the user's selected provider; routed
    providers always use their own default (a global override breaks routing)."""
    if provider == LLM_PROVIDER and os.getenv("MODEL_NAME"):
        return os.getenv("MODEL_NAME")
    return cfg.get("default_model", "gpt-4o-mini")

def _openai_call_kwargs(cfg: dict) -> dict:
    """Timeout for every OpenAI-compatible call; num_ctx cap for Ollama-backed
    models so an 8B model doesn't balloon to a 131k-token 22 GB allocation."""
    kwargs = {"timeout": float(os.getenv("HERMES_LLM_TIMEOUT", "120"))}
    if cfg.get("ollama"):
        kwargs["extra_body"] = {"options": {"num_ctx": int(os.getenv("HERMES_LOCAL_NUM_CTX", "8192"))}}
    return kwargs

class ProviderRouter:
    @staticmethod
    def call(messages: List[Dict], tools_schemas: List[Dict], provider: str = None):
        """Non-streaming call. Returns response object with .content and .tool_calls."""
        provider = provider or LLM_PROVIDER
        cfg = PROVIDER_CONFIGS.get(provider)
        if not cfg: raise ValueError(f"Unsupported provider: {provider}")
        model = _model_for(provider, cfg)
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
            resp = client.chat.completions.create(model=model, messages=om, tools=tools_schemas if tools_schemas else None, tool_choice="auto" if tools_schemas else None, **_openai_call_kwargs(cfg))
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
                resp = client.chat.completions.create(model=model, messages=om, tools=tools_schemas if tools_schemas else None, tool_choice="auto" if tools_schemas else None, **_openai_call_kwargs(cfg))
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
        model = _model_for(provider, cfg)
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
            stream = client.chat.completions.create(model=model, messages=om, tools=tools_schemas if tools_schemas else None, tool_choice="auto" if tools_schemas else None, stream=True, **_openai_call_kwargs(cfg))
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

def ws_token_ok(path_or_url: str, required: str) -> bool:
    """WS auth gate: when a token is required, the connection URL must carry
    token=<required> in its query string. No requirement -> always OK."""
    if not required:
        return True
    query = str(path_or_url or "").split("?", 1)[-1] if "?" in str(path_or_url or "") else ""
    pairs = [p.split("=", 1) for p in query.split("&") if "=" in p]
    return any(k == "token" and v == required for k, v in pairs)

_MCP_SINGLETON = None
def _mcp_client():
    global _MCP_SINGLETON
    if _MCP_SINGLETON is None:
        _MCP_SINGLETON = McpClient()
    return _MCP_SINGLETON

def _available_providers() -> list:
    """Providers whose key is set (or that need none). Presence only — see
    _live_providers for validated liveness."""
    out = []
    for name, cfg in PROVIDER_CONFIGS.items():
        env_key = cfg.get("env", "")
        if not env_key or os.getenv(env_key):
            out.append(name)
    return out

_LIVE_CACHE: Dict[str, Tuple[float, bool]] = {}
_LIVE_TTL = float(os.getenv("HERMES_PROVIDER_PROBE_TTL", "300"))

def _probe_provider(name: str) -> bool:
    """Actually verify a provider answers (key VALIDITY, not just presence).
    Uses free endpoints only (GET /models). Local providers probed without a key."""
    import requests as _rq
    cfg = PROVIDER_CONFIGS.get(name)
    if not cfg:
        return False
    env_key = cfg.get("env", "")
    key = os.getenv(env_key, "") if env_key else ""
    if cfg.get("local"):
        if env_key and not key:
            return False  # gateway (e.g. freellmapi) requires a key that isn't configured
        try:
            base = cfg.get("base", "").rstrip("/")
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            return _rq.get(f"{base}/models", headers=headers, timeout=2).status_code < 500
        except Exception:
            return False
    if env_key and not key:
        return False
    if cfg.get("lib") == "anthropic":
        try:
            r = _rq.get("https://api.anthropic.com/v1/models",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=4)
            return r.status_code == 200
        except Exception:
            return False
    if cfg.get("lib") == "openai" and cfg.get("base"):
        try:
            r = _rq.get(f"{cfg['base'].rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}, timeout=4)
            return r.status_code == 200
        except Exception:
            return False
    if cfg.get("lib") == "openai" and not cfg.get("base"):  # api.openai.com
        try:
            r = _rq.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=4)
            return r.status_code == 200
        except Exception:
            return False
    return bool(key)  # google/cohere etc: fall back to key presence

def _provider_alive(name: str) -> bool:
    now = time.time()
    cached = _LIVE_CACHE.get(name)
    if cached and now - cached[0] < _LIVE_TTL:
        return cached[1]
    alive = _probe_provider(name)
    _LIVE_CACHE[name] = (now, alive)
    return alive

def _live_providers() -> list:
    """Validated-live providers: local endpoints that answer + cloud keys that work.
    Probes run concurrently and are cached for HERMES_PROVIDER_PROBE_TTL seconds."""
    candidates = [n for n, cfg in PROVIDER_CONFIGS.items()
                  if cfg.get("local") or not cfg.get("env") or os.getenv(cfg.get("env", ""))]
    now = time.time()
    stale = [n for n in candidates if n not in _LIVE_CACHE or now - _LIVE_CACHE[n][0] >= _LIVE_TTL]
    if stale:
        with ThreadPoolExecutor(max_workers=min(8, len(stale))) as pool:
            for name, alive in zip(stale, pool.map(_probe_provider, stale)):
                _LIVE_CACHE[name] = (now, alive)
    return [n for n in candidates if _LIVE_CACHE.get(n, (0, False))[1]]

def _plain_llm_call(provider: str, prompt: str) -> str:
    resp = ProviderRouter.call([{"role": "user", "content": prompt}], [], provider)
    return resp.content or ""

def _vision_call(messages: list) -> str:
    provider = os.getenv("HERMES_VISION_PROVIDER", LLM_PROVIDER)
    cfg = PROVIDER_CONFIGS.get(provider, {})
    client = _get_provider_client(provider)
    model = os.getenv("HERMES_VISION_MODEL", cfg.get("default_model", "gpt-4o-mini"))
    resp = client.chat.completions.create(model=model, messages=messages, max_tokens=1000)
    return resp.choices[0].message.content or ""

def _asr_call(audio_path: str) -> str:
    provider = os.getenv("HERMES_ASR_PROVIDER", "openai")
    client = _get_provider_client(provider)
    with open(audio_path, "rb") as fh:
        resp = client.audio.transcriptions.create(model=os.getenv("HERMES_ASR_MODEL", "whisper-1"), file=fh)
    return resp.text

def _tts_call(text: str) -> bytes:
    provider = os.getenv("HERMES_TTS_PROVIDER", "openai")
    client = _get_provider_client(provider)
    resp = client.audio.speech.create(
        model=os.getenv("HERMES_TTS_MODEL", "tts-1"), voice=os.getenv("HERMES_TTS_VOICE", "alloy"), input=text[:4000]
    )
    return resp.content

def _ctx_summarize(prompt: str) -> str:
    """Cheap LLM call used by the ContextEngine for checkpoint summaries."""
    cfg = PROVIDER_CONFIGS.get(LLM_PROVIDER, {})
    client = _get_provider_client(LLM_PROVIDER)
    model = cfg.get("default_model", "gpt-4o-mini")
    resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}], max_tokens=300)
    return resp.choices[0].message.content or ""

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
        required_token = os.getenv("HERMES_WS_TOKEN", "")
        if required_token:
            conn_path = path or getattr(websocket, "path", "") or getattr(getattr(websocket, "request", None), "path", "")
            if not ws_token_ok(conn_path, required_token):
                await websocket.close(code=4401, reason="unauthorized: missing/invalid token")
                return
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
                    # Full agent loop (auto-routing, failover, multi-iteration tools,
                    # immune system, world model, calibration) with live token streaming.
                    text = data["text"]
                    loop = asyncio.get_event_loop()
                    token_q: "asyncio.Queue" = asyncio.Queue()
                    turn_start = len(self.agent.logs)

                    def _on_tok(t):
                        loop.call_soon_threadsafe(token_q.put_nowait, t)
                    self.agent.on_token = _on_tok

                    async def _drain():
                        while True:
                            tok = await token_q.get()
                            if tok is None:
                                break
                            await websocket.send(json.dumps({"type": "token", "content": tok}))
                    drain_task = asyncio.create_task(_drain())
                    try:
                        result = await loop.run_in_executor(None, self.agent.converse, text)
                    except Exception as e:
                        result = f"Error: {e}"
                    finally:
                        self.agent.on_token = None
                        loop.call_soon_threadsafe(token_q.put_nowait, None)
                    await drain_task
                    self.agent.messages = self.agent.convo
                    self.agent.store.save(self.agent.session_id, self.agent.messages)
                    turn_logs = self.agent.logs[turn_start:]
                    tool_calls = [{"name": l["name"], "args": l.get("args", {})} for l in turn_logs if l.get("type") == "tool_call"]
                    routed = next((l for l in reversed(turn_logs) if l.get("type") == "auto_route"), None)
                    changeset = self.agent.changesets.summary()
                    await websocket.send(json.dumps({
                        "type": "response", "content": result,
                        "toolCalls": tool_calls,
                        "routedTo": routed.get("provider") if routed else self.agent.provider,
                        "changeset": changeset if changeset["files"] else None,
                    }))
                elif msg_type == "file_write":
                    p = safe_repo_path(data.get("path", ""))
                    if not p:
                        await websocket.send(json.dumps({"type":"notification", "content":"Invalid path (outside project)"}))
                    else:
                        try:
                            p.parent.mkdir(parents=True, exist_ok=True)
                            p.write_text(data.get("content", ""))
                            await websocket.send(json.dumps({"type":"file_saved", "data":{"path": data.get("path", "")}}))
                        except OSError as exc:
                            await websocket.send(json.dumps({"type":"notification", "content":f"Save failed: {exc}"}))
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
                    elif cmd == "models":
                        cur = self.agent.provider
                        cfg = PROVIDER_CONFIGS.get(cur, {})
                        models = cfg.get("models", [cfg.get("default_model", "unknown")])
                        await websocket.send(json.dumps({"type":"models", "data":{"provider": cur, "models": models, "current": os.environ.get("MODEL_NAME", cfg.get("default_model", ""))}}))
                    elif cmd.startswith("provider:") and not cmd.startswith("provider:test:"):
                        p = cmd.split(":")[1]
                        if p in PROVIDER_CONFIGS:
                            self.agent.provider = p
                            self.agent._provider_pinned = True
                            await websocket.send(json.dumps({"type":"provider", "data":p}))
                        else:
                            await websocket.send(json.dumps({"type":"notification", "content":f"Unknown provider: {p}"}))
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
                    elif cmd == "memory":
                        await websocket.send(json.dumps({"type":"memory", "data":self.agent.context.stats()}))
                    elif cmd.startswith("memory:search:"):
                        q = cmd.split(":", 2)[2]
                        hits = self.agent.context.recall(q)
                        await websocket.send(json.dumps({"type":"memory", "data":[{"kind": h["kind"], "content": h["content"]} for h in hits]}))
                    elif cmd == "dream":
                        report = self.agent.dreamer.dream(self.agent._recent_sessions(), use_llm=True)
                        await websocket.send(json.dumps({"type":"dream", "data":report}))
                    elif cmd == "distill":
                        await websocket.send(json.dumps({"type":"distill", "data":self.agent.distiller.distill()}))
                    elif cmd == "subconscious":
                        await websocket.send(json.dumps({"type":"subconscious", "data":self.agent.subconscious.status()}))
                    elif cmd == "experts":
                        await websocket.send(json.dumps({"type":"experts", "data":{"available": _available_providers(), "profiles": self.agent.orchestra.profiles}}))
                    elif cmd.startswith("blast:"):
                        target = cmd.split(":", 1)[1]
                        await websocket.send(json.dumps({"type":"blast", "data":self.agent.world_model.blast_radius(target)}))
                    elif cmd == "doctor":
                        await websocket.send(json.dumps({"type":"doctor", "data":self.agent.doctor.scan()}))
                    elif cmd == "advisor":
                        await websocket.send(json.dumps({"type":"advisor", "data":self.agent.model_advisor.advise()}))
                    elif cmd == "profile":
                        await websocket.send(json.dumps({"type":"profile", "data":self.agent.profiler.load()}))
                    elif cmd.startswith("profile:build:"):
                        answers = json.loads(cmd.split(":", 2)[2])
                        profile = self.agent.profiler.build(answers)
                        await websocket.send(json.dumps({"type":"profile", "data":profile}))
                    elif cmd.startswith("mcp:tools:"):
                        server_name = cmd.split(":", 2)[2]
                        try:
                            await websocket.send(json.dumps({"type":"mcp_tools", "data":self.agent.mcp.list_tools(server_name)}))
                        except Exception as e:
                            await websocket.send(json.dumps({"type":"notification", "content":f"MCP error: {e}"}))
                    elif cmd.startswith("changeset:old:"):
                        _, _, cs_id, cs_path = cmd.split(":", 3)
                        old = self.agent.changesets.original(cs_id, cs_path)
                        await websocket.send(json.dumps({"type":"changeset_old", "data":{"id": cs_id, "path": cs_path, "old": old}}))
                    elif cmd == "changeset:list":
                        await websocket.send(json.dumps({"type":"changesets", "data":self.agent.changesets.list_turns()}))
                    elif cmd.startswith("changeset:accept:"):
                        _, _, cs_id, cs_path = cmd.split(":", 3)
                        note = self.agent.changesets.accept(cs_id, cs_path)
                        await websocket.send(json.dumps({"type":"changeset_update", "data":{"id": cs_id, "note": note, **self.agent.changesets.summary(cs_id)}}))
                    elif cmd.startswith("changeset:accept_hunk:"):
                        _, _, cs_id, hunk_i, cs_path = cmd.split(":", 4)
                        note = self.agent.changesets.accept_hunk(cs_id, cs_path, int(hunk_i))
                        await websocket.send(json.dumps({"type":"changeset_update", "data":{"id": cs_id, "note": note, **self.agent.changesets.summary(cs_id)}}))
                    elif cmd.startswith("changeset:reject_hunk:"):
                        _, _, cs_id, hunk_i, cs_path = cmd.split(":", 4)
                        note = self.agent.changesets.reject_hunk(cs_id, cs_path, int(hunk_i))
                        await websocket.send(json.dumps({"type":"changeset_update", "data":{"id": cs_id, "note": note, **self.agent.changesets.summary(cs_id)}}))
                    elif cmd.startswith("changeset:reject:"):
                        _, _, cs_id, cs_path = cmd.split(":", 3)
                        note = self.agent.changesets.reject(cs_id, cs_path)
                        await websocket.send(json.dumps({"type":"changeset_update", "data":{"id": cs_id, "note": note, **self.agent.changesets.summary(cs_id)}}))
                    elif cmd.startswith("file:read:"):
                        rel = cmd.split(":", 2)[2]
                        p = safe_repo_path(rel)
                        if p and p.is_file():
                            try:
                                await websocket.send(json.dumps({"type":"file_content", "data":{"path": rel, "content": p.read_text(errors="replace")[:1_000_000]}}))
                            except OSError as exc:
                                await websocket.send(json.dumps({"type":"notification", "content":f"Read failed: {exc}"}))
                        else:
                            await websocket.send(json.dumps({"type":"notification", "content":f"Not a readable project file: {rel}"}))
                    elif cmd == "cancel":
                        self.agent.cancel_event.set()
                        await websocket.send(json.dumps({"type":"notification", "content":"Cancelling current run..."}))
                    elif cmd == "mcp":
                        await websocket.send(json.dumps({"type":"mcp", "data":self.agent.mcp.status()}))
                    elif cmd == "calibration":
                        await websocket.send(json.dumps({"type":"calibration", "data":self.agent.tracker.report()}))
                    elif cmd.startswith("route:"):
                        await websocket.send(json.dumps({"type":"route", "data":self.agent.router.route(cmd.split(":", 1)[1])}))
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
        self._provider_pinned = False
        self.on_token: Optional[Callable[[str], None]] = None  # set by TUIs for live streaming
        self.approve_fn: Optional[Callable[[str, dict, str], bool]] = None  # (tool, args, preview) -> approve?
        self.convo: List[Dict] = []  # persistent multi-turn conversation for converse()
        self.cancel_event = threading.Event()  # set to abort an in-flight run (TUI Ctrl-C / WS cancel)
        self.logs: List[Dict] = []
        self.safety = SafetyManager(SAFETY_MODE)
        self.checkpoints = CheckpointManager()
        self.indexer = CodebaseIndexer()
        self.context = ContextEngine(db_path=DB_FILE, session_id=self.session_id, summarize_fn=_ctx_summarize)
        self.context.attach(HookManager)
        self.events = EventLog(DB_FILE, self.session_id)
        self.events.attach(HookManager)
        self.dreamer = Dreamer(self.context.store, llm_fn=_ctx_summarize)
        self.distiller = Distiller(self.events, SelfLearner.save_skill)
        self.judge = GoalJudge(llm_fn=_ctx_summarize)
        self.subconscious = Subconscious(self.dreamer, self.distiller, session_loader=self._recent_sessions)
        self.subconscious.attach(HookManager)
        self.subconscious.start()
        self.code_intel = CodeIntel()
        self.sem_index = SemanticIndex()
        self.lsp = LspClient()
        self.search = HybridSearch(EmbeddingIndex(), self.sem_index)
        self.world_model = CausalWorldModel()
        self.wm_sentinel = WorldModelSentinel(self.world_model)
        self.wm_sentinel.attach(HookManager)
        self.orchestra = ModelOrchestra(call_fn=_plain_llm_call, available_fn=_live_providers)
        self.vision = Vision(vision_chat_fn=_vision_call)
        self.voice = VoiceIO(transcribe_fn=_asr_call, tts_fn=_tts_call)
        self.mcp = _mcp_client()
        self.doctor = DependencyScanner(provider_configs=PROVIDER_CONFIGS)
        self.model_advisor = ModelAdvisor(provider_configs=PROVIDER_CONFIGS)
        self.profiler = ProfileBuilder(save_skill_fn=SelfLearner.save_skill, memory_store=self.context.store)
        self.changesets = ChangesetManager()
        self.changesets.attach(HookManager)
        self.tracker = CalibrationTracker(DB_FILE)
        self.router = CostAwareRouter(available_fn=_live_providers, tracker=self.tracker)
        def _candidates(prompt: str, n: int) -> Dict[str, str]:
            members = self.orchestra._committee_members(self.orchestra.classify(prompt), n)
            out = {}
            with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
                futures = {pool.submit(_plain_llm_call, m, prompt): m for m in members}
                for future in as_completed(futures):
                    member = futures[future]
                    try: out[member] = future.result()
                    except Exception as e: out[member] = f"(failed: {e})"
            return out
        self.max_mode = MaxMode(candidates_fn=_candidates, judge_fn=_ctx_summarize, tracker=self.tracker)
        HookManager.register("post_tool", self._record_tool_outcomes)
        skills = SelfLearner.load_skills()
        skills_str = f"\nAvailable skills: {', '.join(skills)}" if skills else ""
        persona_str = ""
        addendum = self.profiler.system_addendum()
        if addendum:
            persona_str = f"\n{addendum}"
        self.system_prompt = f"You are Hermes-Ultimate, an autonomous coding assistant with tools for files, shell, Docker, browser, and desktop. You can spawn sub-agents, verify code, and learn new skills. You have persistent cross-session memory: use the remember tool to store important facts, decisions, and user preferences, and recall_memory to search them. Be concise and self-correcting.{persona_str}{skills_str}"
        self._register_advanced_tools()
    def _preview_write(self, tool: str, args: dict) -> str:
        """Human-readable preview of a destructive tool call for approval."""
        try:
            if tool == "write_file":
                path = args.get("filepath", "")
                old = Path(path).read_text(errors="replace") if Path(path).exists() else ""
                return _unified_diff(old, args.get("content", ""), path)
            if tool == "append_file":
                return f"APPEND to {args.get('filepath','')}:\n+ " + str(args.get("content", ""))[:1000]
            if tool == "edit_file_line":
                return (f"EDIT {args.get('filepath','')}\n- {str(args.get('old_string',''))[:400]}\n"
                        f"+ {str(args.get('new_string',''))[:400]}")
            if tool in ("run_command", "git_commit", "git_push", "git_undo"):
                return f"$ {args.get('command') or args.get('message') or tool}"
        except Exception as exc:
            return f"(preview unavailable: {exc})"
        return json.dumps(args)[:500]

    def converse(self, user_text: str, max_iters: int = 10) -> str:
        """Multi-turn: keeps self.convo across calls so the CLI/TUI is a real
        conversation, not a fresh context each turn. Context engine trims as needed."""
        if not self.convo or self.convo[0].get("role") != "system":
            self.convo = [{"role": "system", "content": self.system_prompt}]
        self.cancel_event.clear()
        self.changesets.begin_turn()
        self.convo.append({"role": "user", "content": _expand_mentions(user_text)})
        result = self.run_loop(self.convo, max_iters=max_iters)
        self.convo = self.messages  # run_loop grew self.messages with the full exchange
        return result

    def _call_llm(self, messages: List[Dict], schemas: List[Dict], provider: str):
        """One retry with backoff; streams through self.on_token when set."""
        for attempt in (1, 2):
            try:
                if self.on_token:
                    return self._call_llm_stream(messages, schemas, provider)
                return ProviderRouter.call(messages, schemas, provider)
            except _Cancelled:
                raise  # user cancellation is not a retryable failure
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5)

    def _call_llm_stream(self, messages: List[Dict], schemas: List[Dict], provider: str):
        parts: List[str] = []
        final = None
        for chunk, result, usage in ProviderRouter.call_stream(messages, schemas, provider):
            if self.cancel_event.is_set():
                raise _Cancelled()
            if chunk:
                parts.append(chunk)
                try:
                    self.on_token(chunk)
                except Exception:
                    pass
            if result:
                final = result
        if final is None:
            final = type("Response", (), {"content": "".join(parts), "tool_calls": []})()
        elif not getattr(final, "content", None) and parts:
            try:
                final.content = "".join(parts)
            except Exception:
                pass
        return final

    def _next_fallback(self, failed: str) -> Optional[str]:
        """Failover order after a provider dies mid-run: the user's own provider
        first, then any other validated-live provider not yet tried this run."""
        self._failed_providers.add(failed)
        if self.provider not in self._failed_providers:
            return self.provider
        try:
            for p in _live_providers():
                if p not in self._failed_providers:
                    return p
        except Exception:
            pass
        return None

    def _route_provider(self, user_text: str) -> Tuple[str, Optional[int]]:
        """Cost-aware auto-routing for a run: returns (provider, tier or None).
        Disabled via HERMES_AUTO_ROUTE=off, or when the user pinned a provider."""
        if os.getenv("HERMES_AUTO_ROUTE", "on").lower() in ("off", "0", "false"):
            return self.provider, None
        if getattr(self, "_provider_pinned", False) or not user_text:
            return self.provider, None
        try:
            decision = self.router.route(user_text)
        except Exception:
            return self.provider, None
        routed = decision.get("provider")
        if not routed or routed == self.provider:
            return self.provider, None
        self.logs.append({"type": "auto_route", "provider": routed, "tier": decision.get("tier"),
                          "difficulty": decision.get("difficulty"), "reason": decision.get("reason", "")})
        return routed, decision.get("tier")
    def _record_tool_outcomes(self, results: Optional[List[Dict]] = None, **kwargs):
        try:
            for res in results or []:
                output = str(res.get("result") or "")
                success = not ("Error" in output or "ToolError" in output)
                self.tracker.record("tool_run", 0.8, success)
        except Exception:
            pass
    def _recent_sessions(self, k: int = 5) -> List[List[Dict]]:
        out = []
        for sid in self.store.list_sessions()[:k]:
            msgs = self.store.load(sid)
            if msgs:
                out.append(msgs)
        return out
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
            if provider in PROVIDER_CONFIGS:
                self.provider = provider; self._provider_pinned = True
                return f"Switched to {provider} (auto-routing paused; HERMES_AUTO_ROUTE governs)"
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
        @self.registry.register(description="Save an important fact, decision, or preference to persistent cross-session memory. kind: project|decision|note|preference, importance: 0.0-1.0")
        def remember(content: str, kind: str = "project", importance: str = "0.7") -> str:
            return self.context.remember(content, kind, float(importance))
        @self.registry.register(description="Search persistent cross-session memory")
        def recall_memory(query: str, k: str = "5") -> str:
            hits = self.context.recall(query, int(k))
            if not hits:
                return "No matching memories."
            return json.dumps([{"id": h["id"], "kind": h["kind"], "content": h["content"]} for h in hits], indent=1)
        @self.registry.register(description="Show persistent memory statistics")
        def memory_stats() -> str:
            return json.dumps(self.context.stats(), indent=1)
        @self.registry.register(description="Dream now: consolidate recent session experience into persistent memory")
        def dream_now() -> str:
            return json.dumps(self.dreamer.dream(self._recent_sessions(), use_llm=True), indent=1)
        @self.registry.register(description="Distill now: mine repeated tool workflows into reusable skills")
        def distill_now() -> str:
            return json.dumps(self.distiller.distill(), indent=1)
        @self.registry.register(description="Show subconscious (sleep-time compute) status")
        def subconscious_status() -> str:
            return json.dumps(self.subconscious.status(), indent=1)
        @self.registry.register(description="List functions/classes in a source file with line numbers")
        def code_symbols(filepath: str) -> str:
            return json.dumps(self.code_intel.symbols(filepath), indent=1)
        @self.registry.register(description="Find where a function/class is defined across the codebase")
        def find_definition(name: str) -> str:
            return json.dumps(self.code_intel.find_definition(name), indent=1)
        @self.registry.register(description="Find all references to a symbol across the codebase")
        def find_references(name: str, max_results: str = "50") -> str:
            return json.dumps(self.code_intel.references(name, int(max_results)), indent=1)
        @self.registry.register(description="Semantic code search (local embeddings when available, TF-IDF fallback) — better than grep for concepts")
        def semantic_search(query: str, k: str = "8") -> str:
            hits = self.search.search(query, int(k))
            return json.dumps({"mode": self.search.mode(), "hits": hits}, indent=1)
        @self.registry.register(description="LSP go-to-definition: exact definition location for the symbol at file:line:character (1-based)")
        def goto_definition(filepath: str, line: str, character: str) -> str:
            return json.dumps(self.lsp.definition(filepath, int(line), int(character)), indent=1)
        @self.registry.register(description="LSP find-usages: all references to the symbol at file:line:character (1-based)")
        def find_usages(filepath: str, line: str, character: str) -> str:
            return json.dumps(self.lsp.references(filepath, int(line), int(character)), indent=1)
        @self.registry.register(description="Live LSP diagnostics for a file (type errors, unused vars) — deeper than syntax check")
        def live_diagnostics(filepath: str) -> str:
            return json.dumps(self.lsp.diagnostics(filepath), indent=1)
        @self.registry.register(description="Build/refresh the causal world model from git history and imports")
        def build_world_model() -> str:
            return json.dumps(self.world_model.build(), indent=1)
        @self.registry.register(description="Predict blast radius of editing a file: risk score, importers, co-change history")
        def predict_blast_radius(filepath: str) -> str:
            return json.dumps(self.world_model.blast_radius(filepath), indent=1)
        @self.registry.register(description="Route a question to the best expert model (MoE routing across providers). task_type: code|reasoning|vision|cheap|creative|long_context|search or blank for auto")
        def consult_expert(prompt: str, task_type: str = "") -> str:
            return json.dumps(self.orchestra.consult(prompt, task_type), indent=1)
        @self.registry.register(description="Ask a committee of N different expert models and synthesize the best answer")
        def expert_committee(prompt: str, n: str = "3") -> str:
            return json.dumps(self.orchestra.committee(prompt, int(n)), indent=1)
        @self.registry.register(description="Analyze an image file with a vision model")
        def analyze_image(filepath: str, question: str = "Describe this image in detail.") -> str:
            return self.vision.analyze_image(filepath, question)
        @self.registry.register(description="Analyze a video file: samples frames with ffmpeg, describes them with a vision model")
        def analyze_video(filepath: str, question: str = "What happens in this video?") -> str:
            return self.vision.analyze_video(filepath, question)
        @self.registry.register(description="Transcribe an audio file to text")
        def transcribe_audio(filepath: str) -> str:
            return self.voice.transcribe(filepath)
        @self.registry.register(description="Record from the microphone for N seconds and transcribe")
        def listen(seconds: str = "5") -> str:
            return self.voice.listen(int(seconds))
        @self.registry.register(description="Speak text out loud via TTS")
        def speak(text: str) -> str:
            return self.voice.speak(text)
        @self.registry.register(description="List configured MCP servers and their connection status")
        def mcp_servers() -> str:
            return json.dumps(self.mcp.status(), indent=1)
        @self.registry.register(description="List tools exposed by an MCP server (auto-connects)")
        def mcp_tools(server: str) -> str:
            try:
                return json.dumps(self.mcp.list_tools(server), indent=1)
            except Exception as e:
                return f"MCP error: {e}"
        @self.registry.register(description="Invoke a tool on an MCP server. arguments = JSON object string")
        def mcp_invoke(server: str, tool: str, arguments: str = "{}") -> str:
            try:
                return self.mcp.call_tool(server, tool, json.loads(arguments or "{}"))
            except Exception as e:
                return f"MCP error: {e}"
        @self.registry.register(description="Scan this device for missing dependencies and unconfigured providers; shows install fixes")
        def system_doctor() -> str:
            report = self.doctor.scan()
            return self.doctor.summary(report) + "\n\n" + self.doctor.fix_script(report)
        @self.registry.register(description="List which AI models this machine can run (local by hardware specs + cloud by configured keys)")
        def advise_models() -> str:
            return self.model_advisor.render()
        @self.registry.register(description="Show the user's profile (persona, goals) built at first launch")
        def show_profile() -> str:
            profile = self.profiler.load()
            return json.dumps(profile, indent=1) if profile else "No profile yet. Run /profile rebuild in the CLI."
        @self.registry.register(description="Max Mode: generate N answers from different expert models, judge them, return the best")
        def max_mode(prompt: str, n: str = "3") -> str:
            return json.dumps(self.max_mode.run(prompt, int(n)), indent=1)
        @self.registry.register(description="Explain how a task would be routed (difficulty, chosen provider, cost tier)")
        def route_task(prompt: str) -> str:
            return json.dumps(self.router.route(prompt), indent=1)
        @self.registry.register(description="Show the calibration report: predicted confidence vs actual outcomes")
        def calibration_report() -> str:
            return json.dumps(self.tracker.report(), indent=1)
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
        last_user = next((str(m.get("content") or "") for m in reversed(self.messages) if m.get("role") == "user"), "")
        loop_provider, route_tier = self._route_provider(last_user)
        self._failed_providers = set()
        for i in range(max_iters):
            if self.cancel_event.is_set():
                self.cancel_event.clear()
                HookManager.fire("on_stop")
                return "[cancelled]"
            self.logs.append({"type": "llm_call", "iteration": i, "timestamp": time.time()})
            HookManager.fire("pre_llm", messages=self.messages)
            # provider failover happens INSIDE the iteration — a dead provider must
            # not consume the agent's reasoning budget
            while True:
                schemas = self.registry.get_openai_schemas()
                if PROVIDER_CONFIGS.get(loop_provider, {}).get("local"):
                    schemas = [s for s in schemas if s.get("function", {}).get("name") in CORE_TOOLS]
                try:
                    response = self._call_llm(self.messages, schemas, loop_provider)
                    break
                except _Cancelled:
                    self.cancel_event.clear()
                    HookManager.fire("on_stop")
                    return "[cancelled]"
                except Exception as e:
                    HookManager.fire("on_error", error=str(e))
                    if route_tier is not None:
                        self.router.record_outcome(route_tier, success=False)
                    fallback = self._next_fallback(loop_provider)
                    if not fallback:
                        return f"LLM Error: {e}"
                    self.logs.append({"type": "provider_failover", "from": loop_provider, "to": fallback, "error": str(e)[:200]})
                    loop_provider, route_tier = fallback, None
            self.messages.append({"role": "assistant", "content": response.content or ""})
            HookManager.fire("post_llm", content=response.content, tool_calls=getattr(response, "tool_calls", []))
            tcs = getattr(response, "tool_calls", [])
            if not tcs:
                self.logs.append({"type": "llm_response", "iteration": i, "content": (response.content or "")[:200]})
                HookManager.fire("on_stop")
                if route_tier is not None:
                    self.router.record_outcome(route_tier, success=True)
                return response.content or "No response"
            calls = []
            for tc in tcs:
                f = tc.function if hasattr(tc, "function") else tc["function"]
                calls.append({"id": tc.id if hasattr(tc,"id") else tc["id"], "name": f.name if hasattr(f,"name") else f["name"], "args": json.loads(f.arguments if hasattr(f,"arguments") else f["arguments"])})
            # Safety check
            approved_calls = []
            denied_notes = []
            for call in calls:
                ok, reason = self.safety.should_approve(call["name"], call["args"])
                # interactive approval (TUI diff-approve) overrides suggest-mode blocking
                if not ok and self.approve_fn and _is_destructive(call["name"]):
                    if self.approve_fn(call["name"], call["args"], self._preview_write(call["name"], call["args"])):
                        ok = True
                if ok:
                    approved_calls.append(call)
                else:
                    self.logs.append({"type": "approval_needed", "id": reason, "tool": call["name"]})
                    denied_notes.append(f"{call['name']} (denied by user)")
            if not approved_calls:
                if self.approve_fn and denied_notes:
                    # feed the denial back so the agent can adapt instead of dead-ending
                    self.messages.append({"role": "user", "content": f"[USER DENIED] {', '.join(denied_notes)}. Suggest an alternative or ask what to do."})
                    continue
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
                verdict = self.judge.verdict(self.goal_manager.goal, self.messages)
                try:
                    self.tracker.record("goal_judge", float(verdict.get("confidence", 0.5)), bool(verdict.get("complete", True)))
                except Exception:
                    pass
                if verdict.get("complete", True):
                    HookManager.fire("on_stop")
                    suffix = f" (judge: {verdict['reason'][:120]})" if verdict.get("reason") else ""
                    return f"Goal Complete: {self.goal_manager.goal}{suffix}"
                self.goal_manager.completed = False
                self.logs.append({"type": "judge_rejection", "reason": verdict.get("reason", ""), "iteration": i})
                self.messages.append({"role": "user", "content": f"[JUDGE] Goal NOT satisfied: {verdict.get('reason', 'insufficient evidence of completion')}. Continue working until truly complete."})
        HookManager.fire("on_stop")
        return "Max iterations reached."
    def first_launch_setup(self):
        """First-run onboarding: doctor scan, profile interview, model advisor."""
        print("\n=== Welcome to Hermes! First-launch setup (press Enter to skip any question) ===\n")
        try:
            report = self.doctor.scan()
            print(self.doctor.summary(report))
            if report["missing"]:
                print("\nRun /doctor anytime for install commands.\n")
            answers = self.profiler.interview(lambda q: input(f"{q}\n> "))
            if any(answers.values()):
                profile = self.profiler.build(answers)
                print(f"\nProfile built: {profile['persona_label']}")
                if profile["skills_created"]:
                    print(f"Pre-built skills for you: {', '.join(profile['skills_created'])}")
                addendum = self.profiler.system_addendum()
                if addendum:
                    self.system_prompt += f"\n{addendum}"
            else:
                print("Skipped profiling — run /profile rebuild anytime.")
            print("\n" + self.model_advisor.render())
            print("\n=== Setup complete. Ask me anything. ===\n")
        except (EOFError, KeyboardInterrupt):
            print("\nSetup skipped.")
        except Exception as exc:
            print(f"Setup error (continuing anyway): {exc}")

    COMMANDS_HELP = "/goal, /multitask, /kanban, /browser, /desktop, /record, /compose, /provider, /checkpoint, /index, /search, /safety, /memory, /remember, /dream, /distill, /subconscious, /blast, /experts, /max, /route, /calibration, /see, /say, /listen, /doctor, /models, /profile, /mcp, /reset, exit"

    def handle_command(self, u: str, ask_fn: Callable[[str], str] = input) -> Optional[str]:
        """UI-agnostic slash-command dispatch. Returns output text for a handled
        command ('' for silent success), or None when `u` is not a command and
        should go to the LLM. Used by the plain CLI, the rich TUI, and tests."""
        if u == "/reset":
            self.messages = [{"role":"system","content":self.system_prompt}]
            self.convo = []
            return "Reset."
        if u.startswith("/provider"):
            p = u.split(" ", 1)[1] if " " in u else ""
            if p in PROVIDER_CONFIGS:
                self.provider = p; self._provider_pinned = True
                return f"Switched to {p} (pinned — auto-routing off for this session)"
            return f"Available: {', '.join(PROVIDER_CONFIGS.keys())}"
        if u.startswith("/kanban"):
            parts = u.split()
            if len(parts) >= 3 and parts[1] == "add": return self.kanban.add_task(parts[2]).id
            if len(parts) >= 2 and parts[1] == "show": return json.dumps(self.kanban.get_board_state(), indent=2)
            return None  # fall through to LLM (historic behavior)
        if u.startswith("/goal"):
            self.goal_manager = GoalManager(u.split(" ",1)[1] if " " in u else "")
            return self.run_loop([{"role":"system","content":self.system_prompt},{"role":"user","content":f"Goal: {self.goal_manager.goal}. Work until COMPLETE."}])
        if u.startswith("/multitask"):
            rest = u.split(" ",1)[1] if " " in u else ""
            tasks = [{"name":f"T-{i}","prompt":t.strip()} for i,t in enumerate(rest.split(",") if "," in rest else rest.split("|"))]
            return json.dumps(ParallelExecutor.run(tasks, self), indent=2)
        if u.startswith("/browser"):
            p = u.split(" ",2)
            if len(p) == 3 and p[1] == "goto": return str(asyncio.run(self.browser.goto(p[2])))
            if len(p) >= 2 and p[1] == "screenshot": return str(asyncio.run(self.browser.screenshot()))
            return ""
        if u.startswith("/desktop open"):
            return str(DesktopController.open_app(u.split("open ",1)[1]))
        if u.startswith("/record"):
            parts = u.split(" ",2)
            if len(parts) >= 3:
                SelfLearner.start_recording(parts[1], parts[2]); return f"Recording '{parts[1]}'"
            return "Usage: /record <name> <desc>"
        if u == "/stop_record": return SelfLearner.stop_recording(self.provider)
        if u.startswith("/compose"):
            rest = u.split(" ",2)
            if len(rest) >= 3:
                names = [s.strip() for s in rest[1].split(",")]
                return SelfLearner.compose_workflow(names, rest[2], self.provider)
            return None
        if u.startswith("/checkpoint"):
            parts = u.split()
            if len(parts) >= 2 and parts[1] == "create":
                return self.checkpoints.create_checkpoint(parts[2] if len(parts) > 2 else "")
            if len(parts) >= 2 and parts[1] == "list":
                return json.dumps(self.checkpoints.list_checkpoints(), indent=2)
            if len(parts) >= 3 and parts[1] == "restore":
                return self.checkpoints.restore_checkpoint(parts[2])
            return None
        if u.startswith("/index"): return self.indexer.index_project()
        if u.startswith("/search"):
            return json.dumps(self.indexer.search(u.split(" ", 1)[1] if " " in u else ""), indent=2)
        if u.startswith("/max"):
            prompt_text = u.split(" ", 1)[1] if " " in u else ""
            return json.dumps(self.max_mode.run(prompt_text), indent=1) if prompt_text else "Usage: /max <prompt>"
        if u.startswith("/route"):
            prompt_text = u.split(" ", 1)[1] if " " in u else ""
            return json.dumps(self.router.route(prompt_text), indent=1) if prompt_text else "Usage: /route <prompt>"
        if u == "/calibration": return json.dumps(self.tracker.report(), indent=1)
        if u == "/doctor":
            report = self.doctor.scan()
            return self.doctor.summary(report) + "\n\n" + self.doctor.fix_script(report)
        if u == "/models": return self.model_advisor.render()
        if u.startswith("/profile"):
            if "rebuild" in u:
                answers = self.profiler.interview(lambda q: ask_fn(f"{q}\n> "))
                profile = self.profiler.build(answers)
                return f"Profile rebuilt: {profile['persona_label']} (skills: {', '.join(profile['skills_created']) or 'none'})"
            profile = self.profiler.load()
            return json.dumps(profile, indent=1) if profile else "No profile. Use: /profile rebuild"
        if u.startswith("/mcp"):
            parts = u.split(" ", 3)
            sub = parts[1] if len(parts) > 1 else "list"
            if sub == "list": return json.dumps(self.mcp.status(), indent=1)
            if sub == "tools" and len(parts) > 2:
                try: return json.dumps(self.mcp.list_tools(parts[2]), indent=1)
                except Exception as e: return f"MCP error: {e}"
            if sub == "call" and len(parts) > 3:
                server_tool = parts[2].split("/", 1)
                if len(server_tool) == 2:
                    try: return self.mcp.call_tool(server_tool[0], server_tool[1], json.loads(parts[3]))
                    except Exception as e: return f"MCP error: {e}"
                return "Usage: /mcp call <server>/<tool> <json-args>"
            return "Usage: /mcp list | /mcp tools <server> | /mcp call <server>/<tool> <json-args>"
        if u.startswith("/blast"):
            target = u.split(" ", 1)[1] if " " in u else ""
            return json.dumps(self.world_model.blast_radius(target), indent=1) if target else "Usage: /blast <file>"
        if u.startswith("/experts"):
            rest = u.split(" ", 1)[1] if " " in u else ""
            if rest: return json.dumps(self.orchestra.committee(rest), indent=1)
            return json.dumps({"available": _available_providers(), "profiles": self.orchestra.profiles}, indent=1)
        if u.startswith("/see"):
            parts = u.split(" ", 2)
            if len(parts) >= 2:
                return self.vision.analyze_image(parts[1], parts[2] if len(parts) > 2 else "Describe this image in detail.")
            return "Usage: /see <image> [question]"
        if u.startswith("/say"):
            text = u.split(" ", 1)[1] if " " in u else ""
            return self.voice.speak(text) if text else "Usage: /say <text>"
        if u.startswith("/listen"):
            parts = u.split()
            secs = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
            heard = self.voice.listen(secs)
            out = f"Heard: {heard}"
            if heard and not heard.endswith(("not configured.", "failed.")) and "not " not in heard[:30]:
                result = self.run_loop([{"role":"system","content":self.system_prompt},{"role":"user","content":heard}])
                out += f"\n\n{result}"
            return out
        if u == "/dream": return json.dumps(self.dreamer.dream(self._recent_sessions(), use_llm=True), indent=1)
        if u == "/distill": return json.dumps(self.distiller.distill(), indent=1)
        if u == "/subconscious": return json.dumps(self.subconscious.status(), indent=1)
        if u.startswith("/remember"):
            text = u.split(" ", 1)[1] if " " in u else ""
            return self.context.remember(text) if text else "Usage: /remember <fact>"
        if u.startswith("/memory"):
            query = u.split(" ", 1)[1] if " " in u else ""
            if query:
                hits = self.context.recall(query)
                return json.dumps([{"kind": h["kind"], "content": h["content"]} for h in hits], indent=1) if hits else "No matching memories."
            return json.dumps(self.context.stats(), indent=1)
        if u.startswith("/safety"):
            parts = u.split()
            if len(parts) >= 2:
                self.safety.mode = parts[1]
                return f"Safety mode: {self.safety.mode}"
            return f"Current safety mode: {self.safety.mode}"
        return None

    def chat(self):
        import uuid
        sid = self.session_id
        if not self.profiler.exists() and sys.stdin.isatty():
            self.first_launch_setup()
        print(f"Hermes-Ultimate | Session: {sid} | Provider: {self.provider} ({MODEL_NAME})")
        print(f"Available providers: {', '.join(PROVIDER_CONFIGS.keys())}")
        print(f"Safety mode: {self.safety.mode}")
        print(f"Commands: {self.COMMANDS_HELP}\n")
        while True:
            try: u = input("You: ").strip()
            except (EOFError, KeyboardInterrupt): break
            if not u: continue
            if u == "exit": break
            handled = self.handle_command(u)
            if handled is not None:
                if handled: print(handled)
                continue
            try:
                result = self.converse(u)
            except KeyboardInterrupt:
                print("\n[interrupted]\n"); continue
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
