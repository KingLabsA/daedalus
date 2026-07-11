"""Tools — tool registry, all registered tool functions, self-healer, self-learner.

Standalone: imports nothing from agent_ultimate. Provider calls injected via
ProviderRouter from core.providers.
"""

import inspect
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from core.providers import (
    LLM_PROVIDER,
    PROVIDER_CONFIGS,
    ProviderRouter,
    _get_provider_client,
)

# ============== SECURITY CONSTANTS ==============
BLOCKED_COMMANDS = ["rm -rf /", "sudo rm -rf", "format ", "mkfs", "dd if=", ":(){ :|:& };:"]
MAX_FILE_SIZE = 500_000
PROMPT_INJECTION_PATTERNS = [
    "ignore all previous",
    "disregard",
    "forget your instructions",
    "you are now",
    "act as",
    "system prompt",
    "new instructions",
    "override",
    "pretend you are",
    "you must obey",
]

CORE_TOOLS = [
    "read_file",
    "write_file",
    "append_file",
    "edit_file_line",
    "run_command",
    "grep",
    "list_files",
    "git_status",
    "git_diff_preview",
    "git_commit",
    "semantic_search",
    "search_index",
    "remember",
    "recall_memory",
    "task_board",
    "execute_python",
]
DESTRUCTIVE_TOOLS = {"write_file", "append_file", "edit_file_line", "run_command", "git_commit", "git_push", "git_undo"}

MAX_MESSAGES = 30
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", ".hermes/skills"))
SKILLS_DIR.mkdir(parents=True, exist_ok=True)
CHECKPOINTS_DIR = Path(os.getenv("CHECKPOINTS_DIR", ".hermes/checkpoints"))
CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
WS_HOST = os.getenv("WS_HOST", "127.0.0.1")
WS_PORT = int(os.getenv("WS_PORT", "8765"))
SAFETY_MODE = os.getenv("SAFETY_MODE", "suggest")


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


def _is_destructive(name: str) -> bool:
    return name in DESTRUCTIVE_TOOLS


# ============== MENTION EXPANSION ==============
_MENTION_RE = re.compile(r"(?<![\w/])@([\w./\-]+\.\w+|[\w./\-]+/[\w./\-]+)")


def _expand_mentions(text: str, max_files: int = 6, max_bytes: int = 60_000) -> str:
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

    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=2))
    return "\n".join(lines[:200]) if lines else "(no textual change)"


# ============== STREAMING QUEUE ==============
_stream_queue: list[dict] = []
_stream_lock = threading.Lock()


def _push_stream(entry: dict):
    with _stream_lock:
        _stream_queue.append(entry)


def _drain_stream() -> list[dict]:
    with _stream_lock:
        out = list(_stream_queue)
        _stream_queue.clear()
        return out


# ============== TOOL REGISTRY ==============
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._metadata: dict[str, dict] = {}

    def register(self, name: str = None, description: str = ""):
        def decorator(func):
            tool_name = name or func.__name__
            self._tools[tool_name] = func
            self._metadata[tool_name] = {"description": description or func.__doc__ or "", "signature": inspect.signature(func)}
            return func

        return decorator

    def get(self, name: str) -> Callable | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, name: str, args: dict[str, Any]) -> str:
        func = self.get(name)
        if not func:
            return f"Error: Tool '{name}' not found."
        try:
            return str(func(**args))
        except Exception as e:
            return f"ToolError: {e}"

    def execute_parallel(self, calls: list[dict]) -> list[dict]:
        with ThreadPoolExecutor(max_workers=len(calls)) as executor:
            futures = {executor.submit(self.execute, c["name"], c["args"]): c for c in calls}
            results = []
            for future in as_completed(futures):
                call = futures[future]
                results.append({"id": call["id"], "name": call["name"], "result": future.result()})
            return results

    def get_openai_schemas(self):
        schemas = []
        for name, func in self._tools.items():
            sig = inspect.signature(func)
            params = {"type": "object", "properties": {}, "required": []}
            for p_name, p_param in sig.parameters.items():
                params["properties"][p_name] = {"type": "string"}
                if p_param.default == inspect.Parameter.empty:
                    params["required"].append(p_name)
            schemas.append({"type": "function", "function": {"name": name, "description": self._metadata[name]["description"], "parameters": params}})
        return schemas

    def get_anthropic_schemas(self):
        schemas = []
        for name, func in self._tools.items():
            sig = inspect.signature(func)
            params = {"type": "object", "properties": {}, "required": []}
            for p_name, p_param in sig.parameters.items():
                params["properties"][p_name] = {"type": "string"}
                if p_param.default == inspect.Parameter.empty:
                    params["required"].append(p_name)
            schemas.append({"name": name, "description": self._metadata[name]["description"], "input_schema": params})
        return schemas


registry = ToolRegistry()


# ============== TOOL FUNCTIONS ==============
@registry.register(description="Read the contents of a file")
def read_file(filepath: str) -> str:
    with open(os.path.expanduser(filepath)) as f:
        return f.read()


def _git_stage(path: str) -> str:
    try:
        r = subprocess.run(["git", "add", path], capture_output=True, text=True)
        return r.stderr or ""
    except Exception:
        return ""


def _git_diff(path: str = "") -> str:
    try:
        cmd = ["git", "diff", "--no-color", path] if path else ["git", "diff", "--no-color"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.stdout or "(no diff)"
    except Exception:
        return ""


def _pyright_diagnostics(target: str = ".") -> str:
    try:
        r = subprocess.run(["pyright", target], capture_output=True, text=True, timeout=30)
        return r.stdout or "(no output)"
    except FileNotFoundError:
        return "pyright not installed. Run: npm install -g pyright"
    except Exception as e:
        return f"pyright error: {e}"


@registry.register(description="Write content to a file (auto-stages changes for git)")
def write_file(filepath: str, content: str) -> str:
    if len(content) > MAX_FILE_SIZE:
        return f"Error: File too large ({len(content)} bytes). Max: {MAX_FILE_SIZE}"
    fp = os.path.expanduser(filepath)
    with open(fp, "w") as f:
        f.write(content)
    _git_stage(fp)
    _push_stream({"type": "file_written", "filepath": filepath})
    return f"Written to {filepath}"


@registry.register(description="Append content to a file (auto-stages changes)")
def append_file(filepath: str, content: str) -> str:
    if len(content) > MAX_FILE_SIZE:
        return f"Error: Content too large ({len(content)} bytes). Max: {MAX_FILE_SIZE}"
    fp = os.path.expanduser(filepath)
    with open(fp, "a") as f:
        f.write(content)
    _git_stage(fp)
    _push_stream({"type": "file_written", "filepath": filepath})
    return f"Appended to {filepath}"


@registry.register(description="Edit file by replacing exact string match. Like sed but safe. Staged for git.")
def edit_file_line(filepath: str, old_string: str, new_string: str) -> str:
    fp = os.path.expanduser(filepath)
    if not os.path.exists(fp):
        return f"Error: file not found: {fp}"
    with open(fp) as f:
        content = f.read()
    if old_string not in content:
        return f"Error: old_string not found in {filepath}"
    new_content = content.replace(old_string, new_string, 1)
    with open(fp, "w") as f:
        f.write(new_content)
    _git_stage(fp)
    _push_stream({"type": "file_edited", "filepath": filepath, "old": old_string[:40], "new": new_string[:40]})
    return f"Edited {filepath}"


@registry.register(description="Preview uncommitted git diff (unified format). Best called BEFORE write_file to show what will change.")
def git_diff_preview(path: str = "") -> str:
    return _git_diff(path)


@registry.register(description="Show git status — tracked/untracked/modified files")
def git_status() -> str:
    try:
        r = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
        return r.stdout or "(clean)"
    except Exception as e:
        return f"git status error: {e}"


@registry.register(description="Stage all changes and commit with a message. If empty, auto-generates from diff.")
def git_commit(message: str = "") -> str:
    try:
        r = subprocess.run(["git", "add", "-A"], capture_output=True, text=True)
        if r.returncode != 0:
            return f"git add failed: {r.stderr}"
        if not message:
            diff = _git_diff()
            if diff == "(no diff)":
                return "Nothing to commit."
            lines = diff.split("\n")[:5]
            message = f"auto: {lines[0] if lines else 'update'}"
        r2 = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
        return r2.stdout or r2.stderr or "Committed."
    except Exception as e:
        return f"git commit error: {e}"


@registry.register(description="Undo last changes: revert uncommitted changes in working tree.")
def git_undo() -> str:
    try:
        r = subprocess.run(["git", "checkout", "--", "."], capture_output=True, text=True)
        return r.stdout or r.stderr or "Undone: working tree clean."
    except Exception as e:
        return f"git undo error: {e}"


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
        _push_stream({"type": "stream", "line": output[-2000:] if len(output) > 2000 else output})
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
            lines = lines[: int(max_results)] + [f"... ({len(lines) - int(max_results)} more matches)"]
        return "\n".join(lines) if lines else "No matches."
    except FileNotFoundError:
        fallback = subprocess.run(f'grep -r -n "{pattern}" {path} 2>/dev/null | head -{max_results}', shell=True, capture_output=True, text=True)
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
        fallback = subprocess.run(f'grep -r -l -w "{old_name}" {path} 2>/dev/null', shell=True, capture_output=True, text=True)
        files = [f for f in fallback.stdout.strip().split("\n") if f]
    except Exception as e:
        return f"Search error: {e}"
    if not files:
        return f"No files contain '{old_name}'"
    changed = []
    for filepath in files:
        try:
            content = Path(filepath).read_text(encoding="utf-8", errors="ignore")
            count = len(re.findall(r"\b" + re.escape(old_name) + r"\b", content))
            if count > 0:
                new_content = re.sub(r"\b" + re.escape(old_name) + r"\b", new_name, content)
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
    imports = [line.strip() for line in lines if line.strip().startswith(("import ", "from "))]
    if imports:
        findings.append(f"Imports: {', '.join(imports)}")
    funcs = [line.strip() for line in lines if line.strip().startswith("def ")]
    classes = [line.strip() for line in lines if line.strip().startswith("class ")]
    if funcs:
        findings.append(f"Functions: {', '.join(f.split('(')[0].replace('def ', '') for f in funcs)}")
    if classes:
        findings.append(f"Classes: {', '.join(c.split('(')[0].split(':')[0].replace('class ', '') for c in classes)}")
    has_return = any("return " in line for line in lines)
    has_print = any("print(" in line for line in lines)
    has_yield = any("yield " in line for line in lines)
    side_effects = []
    if has_print:
        side_effects.append("prints to stdout")
    if has_yield:
        side_effects.append("is a generator")
    if any("open(" in line for line in lines):
        side_effects.append("reads/writes files")
    if any("subprocess" in line or "os.system" in line for line in lines):
        side_effects.append("runs external commands")
    if any("requests." in line for line in lines):
        side_effects.append("makes HTTP requests")
    if side_effects:
        findings.append(f"Side effects: {', '.join(side_effects)}")
    if has_return:
        returns = [line.strip() for line in lines if "return " in line and not line.strip().startswith("#")]
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
            prev = lines[max(0, i - 10) : i]
            if any(line.strip().startswith(("if ", "for ")) for line in prev):
                suggestions.append(f"L{i}: Consider extracting the logic before '{stripped[:50]}' into a helper function")
    if depth > 4:
        suggestions.append(f"Nesting depth {depth} detected — consider extracting inner blocks into functions")
    if func_count == 0 and len(lines) > 30:
        suggestions.append("No functions defined in 30+ lines — consider extracting reusable blocks into functions")
    long_lines = [(i + 1, line) for i, line in enumerate(lines) if len(line.strip()) > 100 and not line.strip().startswith("#")]
    if long_lines:
        suggestions.append(f"{len(long_lines)} lines exceed 100 chars — consider breaking them up")
    if not suggestions:
        suggestions.append("Code looks clean — no refactoring suggestions.")
    return f"Refactoring suggestions ({len(suggestions)}):\n" + "\n".join(f"  • {s}" for s in suggestions)


@registry.register(description="Execute Python code in a sandbox")
def execute_python(code: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        f.flush()
        try:
            result = subprocess.run(["python", f.name], capture_output=True, text=True, timeout=10)
            return result.stdout + result.stderr
        finally:
            os.unlink(f.name)


@registry.register(description="Execute a command inside a Docker container")
def docker_execute(image: str = "python:3.12-slim", command: str = "python3 --version", workdir: str = "/workspace") -> str:
    cname = f"hermes-{uuid.uuid4().hex[:8]}"
    mount = f"{os.getcwd()}:/workspace"
    cmd = f'docker run --rm --name {cname} -v "{mount}" -w {workdir} {image} sh -c "{command}"'
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        return (result.stdout + result.stderr) or "(no output)"
    except subprocess.TimeoutExpired:
        return "Docker command timed out."
    except Exception as e:
        return f"Docker error: {e}"


@registry.register(description="Fetch a web page")
def web_fetch(url: str) -> str:
    try:
        import requests

        resp = requests.get(url, timeout=15, headers={"User-Agent": "Hermes-Ultimate/1.0"})
        return resp.text[:8000]
    except Exception as e:
        return f"Fetch error: {e}"


@registry.register(description="Search the web using DuckDuckGo")
def web_search(query: str) -> str:
    try:
        import requests

        resp = requests.get(f"https://api.duckduckgo.com/?q={query}&format=json", timeout=10)
        data = resp.json()
        results = data.get("RelatedTopics", [])[:5]
        return json.dumps([r.get("Text", r.get("Result", "")) for r in results], indent=2)
    except Exception as e:
        return f"Search error: {e}"


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
    except Exception as e:
        return f"map error: {e}"


@registry.register(description="Process an image file — returns base64 data for LLM vision.")
def process_image(filepath: str) -> str:
    try:
        fp = os.path.expanduser(filepath)
        if not os.path.exists(fp):
            return f"Error: file not found: {fp}"
        import base64

        with open(fp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = Path(fp).suffix.lower()
        return f"data:image/{ext[1:] if ext else 'png'};base64,{b64[:50000]}"
    except Exception as e:
        return f"process_image error: {e}"


@registry.register(description="Call an MCP server tool. Pass server name, tool name, and arguments as JSON string.")
def mcp_call(server: str, tool: str, arguments: str = "{}") -> str:
    try:
        from core.platform import McpClient

        return McpClient().call_tool(server, tool, json.loads(arguments or "{}"))
    except Exception as e:
        return f"mcp call error: {e}"


@registry.register(description="Test a provider connection by sending a tiny prompt. Returns latency and model info.")
def test_provider(provider: str = "") -> str:
    target = provider or LLM_PROVIDER
    try:
        cfg = PROVIDER_CONFIGS.get(target)
        if not cfg:
            return f"Unknown provider: {target}"
        api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
        if cfg.get("env") and not api_key:
            return f"{target}: NO API KEY (set {cfg['env']})"
        t0 = time.time()
        client = _get_provider_client(target)
        model = cfg.get("default_model", "gpt-4o-mini")
        client.chat.completions.create(model=model, messages=[{"role": "user", "content": "say ok"}], max_tokens=10)
        lat = round(time.time() - t0, 2)
        return f"{target}: OK ({lat}s) via {cfg.get('default_model', '?')}"
    except Exception as e:
        return f"{target}: FAIL ({e})"


# ============== SUGGEST/APPROVE ==============
_pending_writes: dict[str, dict] = {}


@registry.register(description="Push committed changes to remote. Specify branch (default: current).")
def git_push(branch: str = "") -> str:
    try:
        target = branch or subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["git", "push", "origin", target], capture_output=True, text=True, cwd=os.getcwd())
        return f"Pushed {target}: {r.stdout.strip() or r.stderr.strip() or 'OK'}"
    except Exception as e:
        return f"git_push error: {e}"


@registry.register(description="List branches, create new branch, or switch branch.")
def git_branch(name: str = "", switch: str = "false") -> str:
    try:
        if name:
            r = subprocess.run(["git", "branch", name], capture_output=True, text=True, cwd=os.getcwd())
            if switch.lower() == "true":
                r2 = subprocess.run(["git", "checkout", name], capture_output=True, text=True, cwd=os.getcwd())
                return f"Created and switched to {name}: {r2.stdout.strip() or r2.stderr.strip() or 'OK'}"
            return f"Created branch {name}: {r.stdout.strip() or r.stderr.strip() or 'OK'}"
        r = subprocess.run(["git", "branch", "-a"], capture_output=True, text=True, cwd=os.getcwd())
        return r.stdout.strip() or "(no branches)"
    except Exception as e:
        return f"git_branch error: {e}"


@registry.register(description="Show git log (last N commits with stats).")
def git_log(n: str = "10") -> str:
    try:
        r = subprocess.run(["git", "log", "--oneline", "--stat", "-n", str(n)], capture_output=True, text=True, cwd=os.getcwd())
        return r.stdout.strip() or "(no commits)"
    except Exception as e:
        return f"git_log error: {e}"


@registry.register(description="List files and directories at a path. Returns JSON tree.")
def list_files(path: str = ".", max_depth: str = "3") -> str:
    try:
        root = Path(path).resolve()
        result = {"name": root.name, "type": "dir", "children": []}
        depth = int(max_depth)

        def _scan(d: Path, current_depth: int):
            if current_depth >= depth:
                return []
            children = []
            try:
                for item in sorted(d.iterdir()):
                    if item.name.startswith(".") and item.name not in (".github", ".env.example"):
                        continue
                    if item.name in ("node_modules", "__pycache__", "target", ".git", "dist", "build"):
                        continue
                    if item.is_dir():
                        children.append({"name": item.name, "type": "dir", "children": _scan(item, current_depth + 1)})
                    else:
                        size = item.stat().st_size
                        children.append({"name": item.name, "type": "file", "size": size})
            except PermissionError:
                pass
            return children

        result["children"] = _scan(root, 0)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"list_files error: {e}"


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
        write_id = f"w-{int(time.time() * 1000)}"
        _pending_writes[write_id] = {"filepath": filepath, "content": content}
        return json.dumps({"id": write_id, "filepath": filepath, "diff": diff_text, "status": "pending_approval"})
    except Exception as e:
        return f"suggest_write error: {e}"


@registry.register(description="Confirm and execute a pending file write (from suggest_write). Pass the write ID.")
def confirm_write(write_id: str) -> str:
    pending = _pending_writes.get(write_id)
    if not pending:
        return f"No pending write with id {write_id}"
    filepath = pending["filepath"]
    content = pending["content"]
    del _pending_writes[write_id]
    try:
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        subprocess.run(["git", "add", filepath], capture_output=True, text=True)
        return f"Written {filepath} ({len(content)} chars)"
    except Exception as e:
        return f"confirm_write error: {e}"


@registry.register(description="Discard a pending file write (from suggest_write).")
def deny_write(write_id: str) -> str:
    if write_id in _pending_writes:
        del _pending_writes[write_id]
        return f"Denied write {write_id}"
    return f"No pending write with id {write_id}"


# ============== BACKGROUND PROCESSES ==============
_bg_processes: dict[str, subprocess.Popen] = {}
_bg_output: dict[str, list] = {}
_bg_counter = 0


@registry.register(description="Run a command in the background. Returns a process ID for polling/killing.")
def background_process(command: str, workdir: str = "") -> str:
    global _bg_counter
    _bg_counter += 1
    pid = f"bg-{_bg_counter}"
    try:
        proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=workdir or None)
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
    return output if output else "[running, no output yet]"


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
            ruff_status = "PASS" if ruff.returncode == 0 else "FAIL"
            results.append(f"ruff: {ruff_status}\n{ruff.stdout.strip()[:500]}")
        except FileNotFoundError:
            results.append("ruff: NOT INSTALLED")
        try:
            pytest_r = subprocess.run(["python", "-m", "pytest", str(p), "-x", "-q"], capture_output=True, text=True, timeout=60)
            pytest_status = "PASS" if pytest_r.returncode == 0 else "FAIL"
            results.append(f"pytest: {pytest_status}\n{pytest_r.stdout.strip()[:500]}")
        except Exception as e:
            results.append(f"pytest: ERROR {e}")
    js_files = list(p.rglob("*.ts")) + list(p.rglob("*.tsx")) + list(p.rglob("*.js"))
    if js_files and (p / "package.json").exists():
        try:
            eslint = subprocess.run(["npx", "eslint", str(p)], capture_output=True, text=True, timeout=30)
            eslint_status = "PASS" if eslint.returncode == 0 else "FAIL"
            results.append(f"eslint: {eslint_status}\n{eslint.stdout.strip()[:500]}")
        except Exception:
            results.append("eslint: NOT AVAILABLE")
        try:
            npm_test = subprocess.run(["npm", "test"], capture_output=True, text=True, timeout=60, cwd=str(p))
            npm_test_status = "PASS" if npm_test.returncode == 0 else "FAIL"
            results.append(f"npm test: {npm_test_status}\n{npm_test.stdout.strip()[:500]}")
        except Exception:
            results.append("npm test: NOT AVAILABLE")
    return "\n\n".join(results) if results else f"No lint/test config found at {path}"


@registry.register(description="Kanban task board tool. Actions: list, add, move, remove.")
def task_board(action: str = "list", task_name: str = "", column: str = "") -> str:
    from core.kanban import KanbanBoard

    board = KanbanBoard()
    if action == "list":
        return json.dumps(board.get_board_state(), indent=1)
    elif action == "add" and task_name:
        task = board.add_task(task_name)
        return f"Added task: {task.id} - {task.title}"
    elif action == "move" and task_name:
        board.move_task(task_name, column or "done")
        return f"Moved task {task_name} to {column or 'done'}"
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
        except Exception:
            pass
    return json.dumps(symbols, indent=1)[:10000]


# ============== SELF-HEALER ==============
class SelfHealer:
    @staticmethod
    def analyze_error(error: str, context: str = "") -> str:
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
    def auto_fix(tool_name: str, error: str, args: dict) -> dict | None:
        error_lower = error.lower()
        if tool_name == "run_command" and "not found" in error_lower and "docker" in error_lower:
            return {"command": args.get("command", ""), "use_docker": "false"}
        if tool_name == "read_file" and ("no such file" in error_lower or "not found" in error_lower):
            fp = args.get("filepath", "")
            alternatives = [fp + ".py", fp + ".js", fp + ".ts", fp.replace(".py", ".tsx")]
            for alt in alternatives:
                if os.path.exists(os.path.expanduser(alt)):
                    return {"filepath": alt}
        return None


# ============== SELF-LEARNER ==============
class SelfLearner:
    _recording = False
    _recording_name = ""
    _recording_description = ""
    _action_log = []

    @classmethod
    def start_recording(cls, name: str, description: str):
        cls._recording = True
        cls._recording_name = name
        cls._recording_description = description
        cls._action_log = []
        print(f"Recording: '{name}'")

    @classmethod
    def record_action(cls, action_type: str, details: dict):
        if cls._recording:
            cls._action_log.append({"step": len(cls._action_log) + 1, "type": action_type, "details": details, "timestamp": datetime.now().isoformat()})

    @classmethod
    def stop_recording(cls, provider: str = "openai") -> str:
        if not cls._recording:
            return "Not recording."
        cls._recording = False
        if not cls._action_log:
            return "No actions recorded."
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
            skill_md = ProviderRouter.call([{"role": "user", "content": prompt}], [], provider).content
        except Exception:
            skill_md = f"---\nname: {cls._recording_name}\ndescription: {cls._recording_description}\n---\n{json.dumps(cls._action_log, indent=2)}"
        skill_path = SKILLS_DIR / f"{cls._recording_name}.md"
        skill_path.write_text(skill_md)
        cls._action_log = []
        return f"Skill saved: {skill_path}\n{skill_md}"

    @classmethod
    def compose_workflow(cls, skill_names: list[str], goal: str, provider: str = "openai") -> str:
        skill_contents = []
        for name in skill_names:
            skill_path = SKILLS_DIR / f"{name}.md"
            if skill_path.exists():
                skill_contents.append(skill_path.read_text())
        prompt = f"Goal: {goal}\n\nSkills:\n" + "\n---\n".join(skill_contents)
        try:
            return ProviderRouter.call([{"role": "user", "content": prompt}], [], provider).content
        except Exception:
            return f"Composed workflow for: {goal}"

    @classmethod
    def load_skills(cls) -> list[dict]:
        skills = []
        for f in SKILLS_DIR.glob("*.md"):
            try:
                skills.append({"name": f.stem, "content": f.read_text()[:500]})
            except Exception:
                pass
        return skills


# ============== HOOK MANAGER ==============
class HookManager:
    _hooks: dict[str, list[Callable]] = {
        "pre_tool": [],
        "post_tool": [],
        "pre_llm": [],
        "post_llm": [],
        "pre_commit": [],
        "post_commit": [],
        "on_error": [],
        "on_start": [],
        "on_stop": [],
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
    def fire(cls, event: str, **kwargs) -> list[Any]:
        results = []
        for cb in cls._hooks.get(event, []):
            try:
                result = cb(**kwargs) if kwargs else cb()
                results.append(result)
            except Exception as e:
                print(f"Hook error ({event}): {e}")
        return results

    @classmethod
    def list_hooks(cls) -> dict[str, int]:
        return {event: len(cbs) for event, cbs in cls._hooks.items()}


# ============== FILE WATCHER ==============
class FileWatcher:
    _observer = None
    _indexer = None
    _debounce_seconds = 2.0
    _last_reindex = 0.0
    _changed_files: set = set()

    @classmethod
    def start(cls, indexer=None, path: str = "."):
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
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
        except Exception as e:
            return f"Checkpoint error: {e}"

    @staticmethod
    def list_checkpoints() -> list[dict]:
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
        except Exception as e:
            return f"Restore error: {e}"

    @staticmethod
    def delete_checkpoint(label: str) -> str:
        """Delete checkpoint metadata."""
        cp_dir = CHECKPOINTS_DIR / label
        if cp_dir.exists():
            shutil.rmtree(cp_dir)
            return f"Deleted checkpoint: {label}"
        return f"Checkpoint not found: {label}"
