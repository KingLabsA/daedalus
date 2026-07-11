"""Tests for core.context — memory store, budgeter, checkpointer, immune system, engine.

All offline: no LLM calls (summarize_fn injectable), tmp DB per test.
Also guards that core.context is importable WITHOUT importing agent_ultimate.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context import (
    Checkpointer,
    ContextEngine,
    ImmuneSystem,
    MemoryStore,
    TokenBudgeter,
    estimate_tokens,
)
from core.context.engine import CTX_BEGIN, CTX_END


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"), str(tmp_path / "memory"))


@pytest.fixture
def engine(tmp_path):
    return ContextEngine(
        db_path=str(tmp_path / "test.db"),
        session_id="sess_test",
        root_dir=str(tmp_path / "memory"),
        max_context_tokens=2000,
    )


# ── Import isolation ─────────────────────────────────────────


def test_core_context_importable_without_agent_ultimate():
    # core.context must not pull in the agent monolith
    assert "core.context" in sys.modules
    for mod in ("core.context.store", "core.context.engine"):
        assert mod in sys.modules
    # importing core.context alone must not have imported agent_ultimate
    # (other tests in the suite may import it; only assert the dependency direction)
    import core.context.engine as eng

    assert "agent_ultimate" not in eng.__dict__.get("__builtins__", {})
    src = Path(eng.__file__).read_text()
    assert "agent_ultimate" not in src


# ── MemoryStore ──────────────────────────────────────────────


def test_memory_add_search_rank(store):
    store.add_memory("The build system uses vite with port 5173", "project", 0.9)
    store.add_memory("User prefers tabs over spaces", "preference", 0.5)
    store.add_memory("Refactored kanban module in June", "note", 0.2)
    hits = store.search_memories("what port does vite use")
    assert hits and "5173" in hits[0]["content"]


def test_memory_kind_validation_and_files(store, tmp_path):
    store.add_memory("something", "bogus_kind", 5.0)
    mems = store.list_memories()
    assert mems[0]["kind"] == "note"  # invalid kind coerced
    assert mems[0]["importance"] == 1.0  # clamped
    assert (tmp_path / "memory" / "MEMORY.md").exists()


def test_memory_delete(store):
    mem_id = store.add_memory("temp fact", "note", 0.5)
    assert store.delete_memory(mem_id) is True
    assert store.search_memories("temp fact") == []


def test_fts_query_injection_safe(store):
    # FTS5 syntax characters must not crash search
    store.add_memory("harmless entry", "note", 0.5)
    for evil in ['" OR 1=1 --', "NEAR( ", "*", '"""', "col:val AND ("]:
        store.search_memories(evil)  # must not raise


# ── Failures / immune system ─────────────────────────────────


def test_failure_dedup_bumps_hits(store):
    fid1 = store.record_failure("run_command", "run_command command=npm test", "Error: ENOENT npm")
    fid2 = store.record_failure("run_command", "run_command command=npm test", "Error: ENOENT npm")
    assert fid1 == fid2
    hits = store.find_antibodies("npm test failing")
    assert hits and hits[0]["hits"] == 2


def test_immune_records_only_errors(store):
    immune = ImmuneSystem(store)
    immune.observe_calls(
        [
            {"id": "a", "name": "read_file", "args": {"filepath": "x.py"}},
            {"id": "b", "name": "run_command", "args": {"command": "pytest"}},
        ]
    )
    recorded = immune.observe_results(
        [
            {"id": "a", "result": "file contents fine"},
            {"id": "b", "result": "ToolError: pytest not found"},
        ]
    )
    assert recorded == 1
    assert store.stats()["failures"] == 1


def test_antibodies_block_rendering(store):
    store.record_failure("write_file", "write_file filepath=/etc/hosts", "Error: permission denied", remedy="use a project-relative path")
    immune = ImmuneSystem(store)
    block = immune.antibodies_for([{"role": "user", "content": "please write_file to /etc/hosts"}])
    assert "[ANTIBODIES]" in block
    assert "permission denied" in block
    assert "remedy" in block


# ── Budgeter ─────────────────────────────────────────────────


def test_estimate_tokens():
    assert estimate_tokens("x" * 400) == 100
    msgs = [{"role": "user", "content": "x" * 400}, {"role": "assistant", "content": "y" * 400}]
    assert estimate_tokens(msgs) == 208  # 100+4 each


def test_budgeter_over_budget_and_clip():
    b = TokenBudgeter(max_context_tokens=1000, output_reserve=200)
    assert b.budget == 800
    small = [{"role": "user", "content": "hi"}]
    big = [{"role": "user", "content": "x" * 4000}]
    assert not b.over_budget(small)
    assert b.over_budget(big)
    clipped = b.clip("z" * 1000, 10)
    assert len(clipped) < 100 and clipped.endswith("[...clipped]")


def test_budgeter_allocate_proportional():
    b = TokenBudgeter(max_context_tokens=1000)
    alloc = b.allocate({"a": 3.0, "b": 1.0}, total=400)
    assert alloc["a"] == 300 and alloc["b"] == 100


# ── Checkpointer ─────────────────────────────────────────────


def _sample_messages():
    return [
        {"role": "system", "content": "You are Hermes"},
        {"role": "user", "content": "Fix the login bug in auth.py"},
        {"role": "assistant", "content": "I will inspect the file."},
        {"role": "tool", "tool_call_id": "1", "content": "read src/auth.py: def login(): ..."},
        {"role": "assistant", "content": "Patched the null check in login()."},
        {"role": "user", "content": "now add a test"},
    ]


def test_checkpoint_build_heuristics(store):
    cp = Checkpointer(store).build(_sample_messages())
    assert cp["goal"].startswith("Fix the login bug")
    assert cp["last_request"] == "now add a test"
    assert "src/auth.py" in cp["files_touched"]
    assert cp["summary"] == ""  # no summarize_fn -> offline


def test_checkpoint_save_and_render(store, tmp_path):
    cper = Checkpointer(store, summarize_fn=lambda p: "did auth work")
    cp = cper.save("sess1", _sample_messages())
    assert cp["summary"] == "did auth work"
    assert store.latest_checkpoint("sess1")["goal"].startswith("Fix the login")
    assert (tmp_path / "memory" / "checkpoint.md").exists()
    rendered = Checkpointer.render(cp)
    assert "[SESSION CHECKPOINT]" in rendered and "src/auth.py" in rendered


def test_checkpoint_summarize_failure_is_swallowed(store):
    def boom(prompt):
        raise RuntimeError("no network")

    cp = Checkpointer(store, summarize_fn=boom).build(_sample_messages())
    assert cp["summary"] == ""


# ── ContextEngine ────────────────────────────────────────────


def test_engine_injection_idempotent(engine):
    engine.store.add_memory("API server runs on port 3002", "project", 0.9)
    messages = [
        {"role": "system", "content": "You are Hermes"},
        {"role": "user", "content": "what port is the API server on?"},
    ]
    engine._on_pre_llm(messages=messages)
    first = messages[0]["content"]
    assert CTX_BEGIN in first and "3002" in first
    engine._on_pre_llm(messages=messages)
    second = messages[0]["content"]
    assert second.count(CTX_BEGIN) == 1  # replaced, not duplicated
    assert second.count(CTX_END) == 1


def test_engine_injection_without_system_message(engine):
    engine.store.add_memory("deploy target is fly.io", "project", 0.9)
    messages = [{"role": "user", "content": "where do we deploy? fly.io?"}]
    engine._on_pre_llm(messages=messages)
    assert messages[0]["role"] == "system" and CTX_BEGIN in messages[0]["content"]


def test_engine_reconstruction_shrinks_and_no_leading_tool(engine):
    messages = [{"role": "system", "content": "You are Hermes"}]
    messages.append({"role": "user", "content": "refactor the parser " + "detail " * 50})
    for i in range(60):
        messages.append({"role": "assistant", "content": f"step {i} " + "x" * 200})
        messages.append({"role": "tool", "tool_call_id": str(i), "content": "result " + "y" * 200})
    before = len(messages)
    engine._on_pre_llm(messages=messages)
    assert len(messages) < before
    assert messages[0]["role"] == "system"
    assert "[CONTEXT RESTORED FROM CHECKPOINT" in messages[1]["content"]
    # kept tail must not start with an orphan tool message
    assert messages[2]["role"] != "tool"
    # checkpoint persisted
    assert engine.store.latest_checkpoint("sess_test") is not None


def test_engine_full_hook_cycle(engine):
    engine._on_pre_tool(calls=[{"id": "c1", "name": "run_command", "args": {"command": "make"}}])
    engine._on_post_tool(results=[{"id": "c1", "result": "Error: make: command not found"}])
    assert engine.store.stats()["failures"] == 1
    messages = [
        {"role": "system", "content": "You are Hermes"},
        {"role": "user", "content": "run make command to build"},
    ]
    engine._on_pre_llm(messages=messages)
    assert "[ANTIBODIES]" in messages[0]["content"]
    engine._on_stop()  # only checkpoints when >=3 live messages; must not raise


def test_engine_handlers_never_raise(engine):
    # garbage inputs must be swallowed, not crash the agent loop
    engine._on_pre_llm(messages=None)
    engine._on_pre_llm(messages=[])
    engine._on_pre_tool(calls=None)
    engine._on_post_tool(results=[{"bad": "shape"}])
    engine._on_stop()


def test_engine_remember_recall_stats(engine):
    out = engine.remember("uses uv not pip", "preference", 0.8)
    assert out.startswith("Remembered #")
    hits = engine.recall("pip or uv?")
    assert hits and "uv" in hits[0]["content"]
    stats = engine.stats()
    assert stats["memories"] == 1 and stats["session_id"] == "sess_test"


def test_engine_resume_checkpoint_shown_once(tmp_path):
    db = str(tmp_path / "test.db")
    root = str(tmp_path / "memory")
    # previous session leaves a checkpoint
    old = ContextEngine(db_path=db, session_id="old", root_dir=root)
    old.checkpointer.save("old", _sample_messages())
    # new session resumes
    new = ContextEngine(db_path=db, session_id="new", root_dir=root)
    messages = [
        {"role": "system", "content": "You are Hermes"},
        {"role": "user", "content": "continue where we left off"},
    ]
    new._on_pre_llm(messages=messages)
    assert "[RESUME]" in messages[0]["content"]
    messages.append({"role": "assistant", "content": "ok"})
    messages.append({"role": "user", "content": "great"})
    new._on_pre_llm(messages=messages)
    assert messages[0]["content"].count("[RESUME]") <= 1
