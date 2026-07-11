"""
E2E WebSocket test suite for Hermes Ultimate.
Run: python tests/test_e2e_ws.py
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
WS_URL = "ws://127.0.0.1:8765"
TIMEOUT = 10
results = {}


async def _send_cmd(ws, cmd, expected_type=None, test_name=None, timeout=TIMEOUT):
    if test_name is None:
        test_name = cmd
    try:
        await ws.send(json.dumps({"type": "command", "command": cmd}))
        resp = await asyncio.wait_for(ws.recv(), timeout=timeout)
        result = json.loads(resp)
        if expected_type and result.get("type") != expected_type:
            results[test_name] = f"FAIL: expected '{expected_type}', got '{result.get('type')}'"
        else:
            results[test_name] = "OK"
        return result
    except asyncio.TimeoutError:
        results[test_name] = "FAIL: timeout"
        return None
    except Exception as e:
        results[test_name] = f"FAIL: {e}"
        return None


async def main():
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "agent_ultimate.py"), "ws"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    await asyncio.sleep(3)
    if proc.poll() is not None:
        out, err = proc.communicate()
        print(f"Server failed:\n{out.decode()[:500]}\n{err.decode()[:500]}")
        return

    try:
        async with websockets.connect(WS_URL) as ws:
            await _send_cmd(ws, "tools", "tools", "tools")
            await _send_cmd(ws, "skills", "skills", "skills")
            await _send_cmd(ws, "kanban", "kanban", "kanban")
            await _send_cmd(ws, "sessions", "sessions", "sessions")
            await _send_cmd(ws, "diff", "diff", "diff")
            await _send_cmd(ws, "lsp", "lsp", "lsp")
            await _send_cmd(ws, "logs", "logs", "logs")
            await _send_cmd(ws, "cost", "cost", "cost")
            await _send_cmd(ws, "files", "files", "files")
            await _send_cmd(ws, "git_branches", "git_branches", "git_branches")
            await _send_cmd(ws, "git_log", "git_log", "git_log")
            await _send_cmd(ws, "hooks", "hooks", "hooks")
            await _send_cmd(ws, "safety:status", "safety_mode", "safety_status")
            await _send_cmd(ws, "safety:mode:suggest", "safety_mode", "safety_suggest")
            await _send_cmd(ws, "safety:mode:plan", "safety_mode", "safety_plan")
            await _send_cmd(ws, "safety:mode:auto", "safety_mode", "safety_auto")
            await _send_cmd(ws, "safety:pending", "pending_approvals", "safety_pending")
            await _send_cmd(ws, "provider:openai", "provider", "provider_openai")
            await _send_cmd(ws, "model:gpt-4o", "model", "model_gpt4o")
            await _send_cmd(ws, "checkpoints", "checkpoints", "cp_list")
            await _send_cmd(ws, "checkpoint:create:e2e-test", "notification", "cp_create")
            await asyncio.sleep(0.5)
            await _send_cmd(ws, "checkpoints", "checkpoints", "cp_list_after")
            await _send_cmd(ws, "index:stats", "index_stats", "idx_stats")
            await _send_cmd(ws, "index:reindex", "notification", "idx_reindex")
            await asyncio.sleep(0.5)
            await _send_cmd(ws, "index:stats", "index_stats", "idx_stats_after")
            await _send_cmd(ws, "index:search:def", "index_results", "idx_search")
            await _send_cmd(ws, "watcher:status", "watcher_status", "watcher_before")
            await _send_cmd(ws, "watcher:start", "notification", "watcher_start")
            await asyncio.sleep(0.5)
            await _send_cmd(ws, "watcher:status", "watcher_status", "watcher_running")
            await _send_cmd(ws, "watcher:stop", "notification", "watcher_stop")
            await _send_cmd(ws, "grep:def:agent_ultimate.py", "grep_results", "grep_def")
            await _send_cmd(ws, "grep:nonexistent_xyz:.", "grep_results", "grep_no_match")
            await _send_cmd(ws, "explain:class Foo", "explain", "explain_code")
            await _send_cmd(ws, "review:eval(x)", "review", "review_code")
            await _send_cmd(ws, "refactor:def f():\n  if True:\n    if True:\n      pass", "refactor", "refactor_code")
            await _send_cmd(ws, "kanban:add:e2e-task", "kanban", "kanban_add")
            await _send_cmd(ws, "kanban", "kanban", "kanban_after_add")
            # New tool E2E tests
            await _send_cmd(ws, "bg:start:echo bg-test-ok", "notification", "bg_start")
            await asyncio.sleep(0.5)
            await _send_cmd(ws, "lint:.", "lint_results", "lint_run", timeout=30)
            await _send_cmd(ws, "task:list", "task_board", "task_list")
            await _send_cmd(ws, "task:add:e2e-ws-task", "task_board", "task_add")
            await _send_cmd(ws, "repo:map:.", "repo_map", "repo_map", timeout=15)
            await _send_cmd(ws, "system_prompt", "system_prompt", "sys_prompt")
            await _send_cmd(ws, "session:save", "notification", "session_save")
            # New Deep Mind command surface (Phases 1-11)
            await _send_cmd(ws, "memory", "memory", "memory_stats")
            await _send_cmd(ws, "subconscious", "subconscious", "subconscious")
            await _send_cmd(ws, "calibration", "calibration", "calibration")
            await _send_cmd(ws, "experts", "experts", "experts")
            await _send_cmd(ws, "doctor", "doctor", "doctor", timeout=20)
            await _send_cmd(ws, "advisor", "advisor", "advisor")
            await _send_cmd(ws, "route:fix a typo", "route", "route")
            await _send_cmd(ws, "blast:agent_ultimate.py", "blast", "blast", timeout=20)
            await _send_cmd(ws, "mcp", "mcp", "mcp")
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    passed = sum(1 for v in results.values() if v == "OK")
    failed = sum(1 for v in results.values() if v != "OK")
    print(f"\n{'=' * 60}")
    print(f"E2E RESULTS: {passed}/{passed + failed} passed")
    print(f"{'=' * 60}")
    for name, result in sorted(results.items()):
        status = "\u2713" if result == "OK" else f"\u2717 {result}"
        print(f"  {status}  {name}")
    print()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
