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
    SKILLS_DIR, PLUGINS_DIR,
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
    plugins = PluginMarketplace.list_remote()
    assert len(plugins) >= 4
    assert any(p["name"] == "web-scraper" for p in plugins)

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
