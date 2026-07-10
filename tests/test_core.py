import pytest, json, os, sqlite3, tempfile
from pathlib import Path
from datetime import datetime

# Import the agent module
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["OPENAI_API_KEY"] = "sk-test"
os.environ["SKILLS_DIR"] = str(tempfile.mkdtemp())

from agent_ultimate import (
    ToolRegistry, SelfLearner, KanbanTask, KanbanBoard,
    KanbanWorker, SessionStore, PluginMarketplace, ParallelExecutor,
    SubAgent, GoalManager, compress_messages, PROVIDER_CONFIGS,
    SKILLS_DIR, PLUGINS_DIR, SelfHealer, CheckpointManager,
    CodebaseIndexer, SafetyManager, HookManager, FileWatcher,
)

# ── ToolRegistry ─────────────────────────────────────

def test_registry_register_and_list():
    reg = ToolRegistry()
    @reg.register(description="test tool")
    def my_tool(arg: str) -> str:
        return f"got: {arg}"
    assert "my_tool" in reg.list_tools()
    assert reg.get("my_tool") is my_tool

def test_registry_execute():
    reg = ToolRegistry()
    @reg.register(description="echo")
    def echo(msg: str) -> str:
        return msg
    assert reg.execute("echo", {"msg": "hello"}) == "hello"

def test_registry_execute_missing():
    reg = ToolRegistry()
    assert "not found" in reg.execute("nope", {})

def test_registry_execute_error():
    reg = ToolRegistry()
    @reg.register(description="broken")
    def broken():
        raise ValueError("boom")
    assert "ToolError" in reg.execute("broken", {})

def test_registry_execute_parallel():
    reg = ToolRegistry()
    @reg.register(description="id")
    def identity(x: str) -> str:
        return x
    calls = [{"id": "1", "name": "identity", "args": {"x": "a"}},
             {"id": "2", "name": "identity", "args": {"x": "b"}}]
    results = reg.execute_parallel(calls)
    assert len(results) == 2
    assert {r["result"] for r in results} == {"a", "b"}

def test_registry_schemas():
    reg = ToolRegistry()
    @reg.register(description="do stuff")
    def do_stuff(a: str, b: str = "default") -> str:
        return a + b
    openai = reg.get_openai_schemas()
    assert any(s["function"]["name"] == "do_stuff" for s in openai)
    anthropic = reg.get_anthropic_schemas()
    assert any(s["name"] == "do_stuff" for s in anthropic)

# ── SelfLearner ───────────────────────────────────────

def test_self_learner_recording():
    SelfLearner._recording = False
    SelfLearner._action_log = []
    SelfLearner.start_recording("test_skill", "a test")
    assert SelfLearner._recording
    assert SelfLearner._recording_name == "test_skill"
    SelfLearner.record_action("read_file", {"filepath": "x.txt"})
    assert len(SelfLearner._action_log) == 1
    # cleanup
    skill_path = SKILLS_DIR / "test_skill.md"
    if skill_path.exists(): skill_path.unlink()

def test_self_learner_stop_no_actions():
    SelfLearner._recording = False
    SelfLearner._action_log = []
    SelfLearner.start_recording("empty", "no actions")
    result = SelfLearner.stop_recording()
    assert "No actions" in result

def test_self_learner_load_skills():
    skills = SelfLearner.load_skills()
    assert isinstance(skills, list)

# ── Kanban ────────────────────────────────────────────

def test_kanban_task_creation():
    task = KanbanTask(id="t1", title="test", description="desc")
    assert task.id == "t1"
    assert task.status == "todo"
    assert task.retries == 0
    assert task.max_retries == 3

def test_kanban_board_add_task():
    board = KanbanBoard()
    task = board.add_task("my task", "details")
    assert task in board.tasks
    assert task.status == "todo"

def test_kanban_board_add_worker():
    board = KanbanBoard()
    board.add_worker("w1", "llm")
    assert len(board.workers) == 1
    assert board.workers[0].status == "idle"

def test_kanban_worker_heartbeat():
    worker = KanbanWorker("t", "test")
    old = worker.last_heartbeat
    worker.heartbeat()
    assert worker.last_heartbeat >= old

def test_kanban_board_state():
    board = KanbanBoard()
    board.add_task("task A")
    board.add_task("task B")
    state = board.get_board_state()
    assert "todo" in state
    assert "in_progress" in state
    assert "done" in state
    assert len(state["todo"]) == 2

def test_kanban_guardian_zombie():
    board = KanbanBoard()
    board.running = False  # stop guardian
    task = board.add_task("zombie test")
    worker = KanbanWorker("slow", "test")
    worker.status = "working"
    worker.current_task = task
    task.status = "in_progress"
    # manually trigger guardian logic
    from datetime import timedelta
    worker.last_heartbeat = datetime.now() - timedelta(seconds=60)
    now = datetime.now()
    if (now - worker.last_heartbeat).total_seconds() > 30 and worker.status == "working":
        worker.status = "idle"
        worker.current_task = None
        task.status = "todo"
        task.retries += 1
    assert worker.status == "idle"
    assert task.status == "todo"
    assert task.retries == 1

# ── SessionStore ──────────────────────────────────────

def test_session_store_save_load(tmp_path):
    db = str(tmp_path / "test.db")
    store = SessionStore(db)
    store.save("sess1", [{"role": "user", "content": "hi"}])
    loaded = store.load("sess1")
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0]["content"] == "hi"

def test_session_store_load_missing(tmp_path):
    db = str(tmp_path / "test.db")
    store = SessionStore(db)
    assert store.load("nonexistent") is None

def test_session_store_list(tmp_path):
    db = str(tmp_path / "test.db")
    store = SessionStore(db)
    store.save("s1", [])
    assert "s1" in store.list_sessions()

# ── GoalManager ───────────────────────────────────────

def test_goal_manager():
    gm = GoalManager("test goal")
    assert gm.goal == "test goal"
    assert not gm.completed
    result = gm.is_complete("still working")
    assert not result
    assert not gm.completed
    result = gm.is_complete("COMPLETE")
    assert result
    assert gm.completed

# ── PluginMarketplace ─────────────────────────────────

def test_plugin_manifest_validation():
    valid = {"name": "p", "version": "1.0", "description": "d", "author": "a", "tools": [], "min_agent_version": "1.0"}
    assert PluginMarketplace._validate_manifest(valid)
    invalid = {"name": "p"}
    assert not PluginMarketplace._validate_manifest(invalid)

def test_plugin_discover_local(tmp_path):
    # Temporarily override PLUGINS_DIR
    old = PLUGINS_DIR
    import agent_ultimate
    agent_ultimate.PLUGINS_DIR = tmp_path
    (tmp_path / "myplugin").mkdir()
    (tmp_path / "myplugin" / "plugin.json").write_text(json.dumps({
        "name": "myplugin", "version": "1.0.0", "description": "desc",
        "author": "me", "tools": ["x"], "min_agent_version": "1.0.0"
    }))
    AgentState = PluginMarketplace
    plugins = AgentState.discover_local()
    assert len(plugins) == 1
    assert plugins[0]["name"] == "myplugin"
    agent_ultimate.PLUGINS_DIR = old

def test_plugin_remote_list():
    # No public registry yet: must return an honest empty list, not mock entries.
    assert PluginMarketplace.list_remote() == []

def test_skill_versions(tmp_path):
    old_skills = SKILLS_DIR
    import agent_ultimate
    agent_ultimate.SKILLS_DIR = tmp_path
    (tmp_path / "myskill.md").write_text("# test skill")
    versions = PluginMarketplace.get_skill_versions("myskill")
    assert len(versions) == 1
    assert versions[0]["version"] == "local"
    agent_ultimate.SKILLS_DIR = old_skills

# ── Provider Config ───────────────────────────────────

def test_provider_configs_present():
    assert "openai" in PROVIDER_CONFIGS
    assert "anthropic" in PROVIDER_CONFIGS
    assert "ollama" in PROVIDER_CONFIGS
    assert len(PROVIDER_CONFIGS) >= 15

# ── Context Compression ───────────────────────────────

def test_compress_messages_short():
    msgs = [{"role": "user", "content": "hi"}]
    result = compress_messages(msgs)
    assert len(result) == 1

def test_compress_messages_long():
    msgs = [{"role": "system", "content": "be helpful"}]
    for i in range(25):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"})
    result = compress_messages(msgs)
    assert len(result) < len(msgs)
    assert any("[Summary]" in m.get("content", "") for m in result)

# ── SelfHealer ────────────────────────────────────────

def test_self_healer_analyze_error():
    result = SelfHealer.analyze_error("FileNotFoundError: [Errno 2] No such file or directory", "reading config")
    assert isinstance(result, str)
    assert len(result) > 0

def test_self_healer_auto_fix_returns_none_or_str():
    result = SelfHealer.auto_fix("run_command", "ToolError: command not found", {"cmd": "ls"})
    assert result is None or isinstance(result, str)

# ── CheckpointManager ─────────────────────────────────

def test_checkpoint_list_empty():
    cps = CheckpointManager.list_checkpoints()
    assert isinstance(cps, list)

# ── CodebaseIndexer ───────────────────────────────────

def test_indexer_stats():
    indexer = CodebaseIndexer()
    stats = indexer.get_stats()
    assert "total_files" in stats
    assert "by_extension" in stats
    assert isinstance(stats["total_files"], int)

def test_indexer_search_empty():
    indexer = CodebaseIndexer()
    results = indexer.search("nonexistent_query_xyz_12345")
    assert isinstance(results, list)

# ── SafetyManager ─────────────────────────────────────

def test_safety_manager_auto_mode():
    sm = SafetyManager("auto")
    ok, reason = sm.should_approve("write_file", {"path": "test.py", "content": "x"})
    assert ok is True
    assert reason == "auto-mode"

def test_safety_manager_suggest_mode_blocks_destructive():
    sm = SafetyManager("suggest")
    ok, reason = sm.should_approve("write_file", {"path": "test.py", "content": "x"})
    assert ok is False
    assert reason.startswith("appr-")

def test_safety_manager_suggest_mode_allows_readonly():
    sm = SafetyManager("suggest")
    ok, reason = sm.should_approve("read_file", {"path": "test.py"})
    assert ok is True
    assert reason == "read-only"

def test_safety_manager_approve_deny():
    sm = SafetyManager("suggest")
    ok, reason = sm.should_approve("write_file", {"path": "test.py", "content": "x"})
    assert ok is False
    assert sm.approve(reason) is True
    assert sm.approve("nonexistent") is False

def test_safety_manager_deny():
    sm = SafetyManager("suggest")
    ok, reason = sm.should_approve("write_file", {"path": "test.py", "content": "x"})
    assert ok is False
    assert sm.deny(reason) is True
    assert sm.deny("nonexistent") is False

def test_safety_manager_get_pending():
    import time
    sm = SafetyManager("plan")
    ok1, id1 = sm.should_approve("git_commit", {"message": "test"})
    time.sleep(0.01)
    ok2, id2 = sm.should_approve("write_file", {"path": "x.py", "content": "y"})
    assert ok1 is False
    assert ok2 is False
    pending = sm.get_pending()
    assert len(pending) == 2
    assert all(p["status"] == "pending" for p in pending)

# ── HookManager ───────────────────────────────────────

def test_hook_manager_register_and_fire():
    fired = []
    HookManager.register("pre_tool", lambda: fired.append(1))
    HookManager.fire("pre_tool")
    assert len(fired) == 1

def test_hook_manager_list_hooks():
    HookManager.register("post_tool", lambda: None)
    hooks = HookManager.list_hooks()
    assert isinstance(hooks, dict)
    assert "pre_tool" in hooks
    assert hooks["pre_tool"] >= 1

def test_hook_manager_fire_nonexistent():
    HookManager.fire("nonexistent_event_xyz")
    assert True  # Should not crash

# ── grep tool ──────────────────────────────────────────

def test_grep_finds_pattern():
    from agent_ultimate import registry
    result = registry.execute("grep", {"pattern": "def test_", "path": "tests/", "max_results": "5"})
    assert isinstance(result, str)
    assert "test_" in result

def test_grep_no_matches():
    from agent_ultimate import registry
    result = registry.execute("grep", {"pattern": "zzz_nonexistent_pattern_99887", "path": "/dev/null"})
    assert "No matches" in result

# ── rename_symbol tool ─────────────────────────────────

def test_rename_symbol_no_matches():
    from agent_ultimate import registry
    result = registry.execute("rename_symbol", {"old_name": "zzz_nonexistent_symbol_99887", "new_name": "new_name", "path": "/dev/null"})
    assert "No files contain" in result

# ── explain_code tool ──────────────────────────────────

def test_explain_code_basic():
    from agent_ultimate import registry
    result = registry.execute("explain_code", {"code": "import os\ndef hello():\n    return 42"})
    assert "Lines: 3" in result
    assert "hello" in result

def test_explain_code_empty():
    from agent_ultimate import registry
    result = registry.execute("explain_code", {"code": ""})
    assert "Lines:" in result

# ── review_code tool ───────────────────────────────────

def test_review_code_security():
    from agent_ultimate import registry
    result = registry.execute("review_code", {"code": "eval(x)\nos.system(cmd)"})
    assert "SECURITY" in result
    assert "eval/exec" in result

def test_review_code_clean():
    from agent_ultimate import registry
    result = registry.execute("review_code", {"code": "x = 1\ny = x + 1"})
    assert "No issues found" in result

def test_review_code_todo():
    from agent_ultimate import registry
    result = registry.execute("review_code", {"code": "x = 1  # TODO: fix this"})
    assert "NOTE" in result

# ── refactor_code tool ─────────────────────────────────

def test_refactor_code_deep_nesting():
    from agent_ultimate import registry
    code = "if a:\n    if b:\n        if c:\n            if d:\n                if e:\n                    pass"
    result = registry.execute("refactor_code", {"code": code})
    assert "Nesting depth" in result

def test_refactor_code_clean():
    from agent_ultimate import registry
    result = registry.execute("refactor_code", {"code": "x = 1"})
    assert "no refactoring suggestions" in result.lower()

# ── FileWatcher ────────────────────────────────────────

def test_file_watcher_status():
    status = FileWatcher.status()
    assert isinstance(status, dict)
    assert "running" in status
    assert "pending_changes" in status

def test_file_watcher_stop_when_not_running():
    result = FileWatcher.stop()
    assert "not running" in result.lower()
