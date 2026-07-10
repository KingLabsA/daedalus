"""Eval-driven deploy gate — verify what the agent built BEFORE it ships.

Detects the project type and runs the right smoke check (build passes / code
compiles / tests green / MCP server handshakes). deploy planning can require
this to pass, so a broken app never gets a deploy command. Standalone,
stdlib-only; the command runner is injectable so tests stay offline.
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .deploy import detect


def _run(cmd: List[str], cwd: str, timeout: int = 600) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-2000:]
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 1, str(exc)


def _has_build_script(p: Path) -> bool:
    try:
        return "build" in json.loads((p / "package.json").read_text()).get("scripts", {})
    except (OSError, ValueError):
        return False


def _py_files(p: Path, limit: int = 200) -> List[Path]:
    out = []
    for f in p.rglob("*.py"):
        if "node_modules" in f.parts or ".git" in f.parts:
            continue
        out.append(f)
        if len(out) >= limit:
            break
    return out


def checks_for(project_dir: str, kind: str = "") -> List[Dict]:
    """The verification steps for a project. Each: {name, kind, cmd|py}."""
    p = Path(project_dir)
    kind = kind or detect(project_dir)
    steps: List[Dict] = []

    node_like = {"static", "next", "node", "tailwind", "shadcn", "supabase", "astro", "svelte", "sveltekit", "web", "react"}
    if kind in node_like or _has_build_script(p):
        if (p / "package.json").exists():
            steps.append({"name": "npm install", "cmd": ["npm", "install", "--no-audit", "--no-fund"], "cwd": str(p)})
            if _has_build_script(p):
                steps.append({"name": "npm run build", "cmd": ["npm", "run", "build"], "cwd": str(p)})

    if kind in ("python", "docker", "api", "cli") or _py_files(p):
        steps.append({"name": "python compile", "py": "compile", "cwd": str(p)})
        if (p / "tests").is_dir() or list(p.glob("test_*.py")) or list(p.glob("*_test.py")):
            steps.append({"name": "pytest", "cmd": [sys.executable, "-m", "pytest", "-q"], "cwd": str(p)})

    if (p / "server.py").exists() and (p / ".hermes" / "mcp.json").exists():
        steps.append({"name": "mcp handshake", "py": "mcp", "cwd": str(p)})

    if kind == "fullstack":
        fe, be = p / "frontend", p / "backend"
        if (fe / "package.json").exists():
            steps.append({"name": "frontend install", "cmd": ["npm", "install"], "cwd": str(fe)})
            steps.append({"name": "frontend build", "cmd": ["npm", "run", "build"], "cwd": str(fe)})
        if (be).exists():
            steps.append({"name": "backend compile", "py": "compile", "cwd": str(be)})
    return steps


def _py_compile(cwd: str) -> Tuple[int, str]:
    import py_compile
    errs = []
    for f in _py_files(Path(cwd)):
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as exc:
            errs.append(str(exc.msg)[:200])
    return (0, f"compiled {len(_py_files(Path(cwd)))} files clean") if not errs else (1, "\n".join(errs[:10]))


def _mcp_handshake(cwd: str) -> Tuple[int, str]:
    try:
        proc = subprocess.Popen([sys.executable, "server.py"], cwd=cwd,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
        proc.stdin.write(req); proc.stdin.flush()
        line = proc.stdout.readline()
        proc.terminate()
        ok = '"result"' in line and "serverInfo" in line
        return (0, "handshake ok") if ok else (1, f"bad handshake: {line[:200]}")
    except Exception as exc:
        return 1, str(exc)


def run_checks(project_dir: str, kind: str = "",
               runner: Callable[[List[str], str, int], Tuple[int, str]] = _run) -> Dict:
    if not Path(project_dir).exists():
        return {"ok": False, "error": f"No such directory: {project_dir}"}
    kind = kind or detect(project_dir)
    steps = checks_for(project_dir, kind)
    if not steps:
        return {"ok": True, "kind": kind, "passed": True, "checks": [],
                "note": "no automated checks for this project kind — passing by default"}
    results, all_pass = [], True
    for step in steps:
        if step.get("py") == "compile":
            code, out = _py_compile(step["cwd"])
        elif step.get("py") == "mcp":
            code, out = _mcp_handshake(step["cwd"])
        else:
            code, out = runner(step["cmd"], step["cwd"], 600)
        passed = code == 0
        all_pass = all_pass and passed
        results.append({"name": step["name"], "passed": passed, "detail": out[-300:] if not passed else "ok"})
    return {"ok": True, "kind": kind, "passed": all_pass, "checks": results}


def gate(project_dir: str = ".", kind: str = "", runner=_run) -> Dict:
    """Verdict used as a deploy gate: {passed, checks, verdict}."""
    r = run_checks(project_dir, kind, runner)
    if not r.get("ok"):
        return r
    failed = [c["name"] for c in r["checks"] if not c["passed"]]
    r["verdict"] = ("PASS — safe to deploy" if r["passed"]
                    else f"BLOCKED — failing checks: {', '.join(failed)}")
    return r
