"""
E2E WebSocket test suite for Hermes Ultimate.
Run: python tests/test_e2e_ws.py
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
WS_URL = "ws://127.0.0.1:8765"
TIMEOUT = 10
results = {}


async def test_cmd(ws, cmd, expected_type=None, test_name=None, timeout=TIMEOUT):
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
            await test_cmd(ws, "tools", "tools", "tools")
            await test_cmd(ws, "skills", "skills", "skills")
            await test_cmd(ws, "kanban", "kanban", "kanban")
            await test_cmd(ws, "sessions", "sessions", "sessions")
            await test_cmd(ws, "diff", "diff", "diff")
            await test_cmd(ws, "lsp", "lsp", "lsp")
            await test_cmd(ws, "logs", "logs", "logs")
            await test_cmd(ws, "cost", "cost", "cost")
            await test_cmd(ws, "files", "files", "files")
            await test_cmd(ws, "git_branches", "git_branches", "git_branches")
            await test_cmd(ws, "git_log", "git_log", "git_log")
            await test_cmd(ws, "hooks", "hooks", "hooks")
            await test_cmd(ws, "safety:status", "safety_mode", "safety_status")
            await test_cmd(ws, "safety:mode:suggest", "safety_mode", "safety_suggest")
            await test_cmd(ws, "safety:mode:plan", "safety_mode", "safety_plan")
            await test_cmd(ws, "safety:mode:auto", "safety_mode", "safety_auto")
            await test_cmd(ws, "safety:pending", "pending_approvals", "safety_pending")
            await test_cmd(ws, "provider:openai", "provider", "provider_openai")
            await test_cmd(ws, "model:gpt-4o", "model", "model_gpt4o")
            await test_cmd(ws, "checkpoints", "checkpoints", "cp_list")
            await test_cmd(ws, "checkpoint:create:e2e-test", "notification", "cp_create")
            await asyncio.sleep(0.5)
            await test_cmd(ws, "checkpoints", "checkpoints", "cp_list_after")
            await test_cmd(ws, "index:stats", "index_stats", "idx_stats")
            await test_cmd(ws, "index:reindex", "notification", "idx_reindex")
            await asyncio.sleep(0.5)
            await test_cmd(ws, "index:stats", "index_stats", "idx_stats_after")
            await test_cmd(ws, "index:search:def", "index_results", "idx_search")
            await test_cmd(ws, "watcher:status", "watcher_status", "watcher_before")
            await test_cmd(ws, "watcher:start", "notification", "watcher_start")
            await asyncio.sleep(0.5)
            await test_cmd(ws, "watcher:status", "watcher_status", "watcher_running")
            await test_cmd(ws, "watcher:stop", "notification", "watcher_stop")
            await test_cmd(ws, "grep:def:agent_ultimate.py", "grep_results", "grep_def")
            await test_cmd(ws, "grep:nonexistent_xyz:.", "grep_results", "grep_no_match")
            await test_cmd(ws, "explain:class Foo", "explain", "explain_code")
            await test_cmd(ws, "review:eval(x)", "review", "review_code")
            await test_cmd(ws, "refactor:def f():\n  if True:\n    if True:\n      pass", "refactor", "refactor_code")
            await test_cmd(ws, "kanban:add:e2e-task", "kanban", "kanban_add")
            await test_cmd(ws, "kanban", "kanban", "kanban_after_add")
            # New tool E2E tests
            await test_cmd(ws, "bg:start:echo bg-test-ok", "notification", "bg_start")
            await asyncio.sleep(0.5)
            await test_cmd(ws, "lint:.", "lint_results", "lint_run", timeout=30)
            await test_cmd(ws, "task:list", "task_board", "task_list")
            await test_cmd(ws, "task:add:e2e-ws-task", "task_board", "task_add")
            await test_cmd(ws, "repo:map:.", "repo_map", "repo_map", timeout=15)
            await test_cmd(ws, "system_prompt", "system_prompt", "sys_prompt")
            await test_cmd(ws, "session:save", "notification", "session_save")
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
    print(f"\n{'='*60}")
    print(f"E2E RESULTS: {passed}/{passed + failed} passed")
    print(f"{'='*60}")
    for name, result in sorted(results.items()):
        status = "\u2713" if result == "OK" else f"\u2717 {result}"
        print(f"  {status}  {name}")
    print()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
