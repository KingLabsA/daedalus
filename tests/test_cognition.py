"""Tests for core.cognition — event log, dream, distill, judge, subconscious.

All offline: fake llm_fns, tmp DBs, sub-second subconscious thresholds.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cognition import Distiller, Dreamer, EventLog, GoalJudge, Subconscious
from core.context import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"), str(tmp_path / "memory"))


@pytest.fixture
def events(tmp_path):
    return EventLog(str(tmp_path / "test.db"), "sess_a")


def test_cognition_has_no_agent_ultimate_dependency():
    import re

    import core.cognition as cog

    for mod_file in Path(cog.__file__).parent.glob("*.py"):
        src = mod_file.read_text()
        assert not re.search(r"^\s*(?:from|import)\s+agent_ultimate", src, re.M), mod_file.name


# ── EventLog ─────────────────────────────────────────────────


def test_eventlog_hook_cycle_and_sequences(events):
    events._on_pre_tool(
        calls=[
            {"id": "1", "name": "read_file", "args": {"filepath": "a.py"}},
            {"id": "2", "name": "edit_file_line", "args": {"filepath": "a.py"}},
        ]
    )
    events._on_post_tool(
        results=[
            {"id": "1", "result": "contents"},
            {"id": "2", "result": "ToolError: no match"},
        ]
    )
    seqs = events.sequences()
    assert seqs == [["read_file", "edit_file_line"]]
    assert events.stats() == {"events": 2, "sessions": 1}


def test_eventlog_multi_session(tmp_path):
    db = str(tmp_path / "test.db")
    a, b = EventLog(db, "s1"), EventLog(db, "s2")
    a.record("grep")
    a.record("read_file")
    b.record("run_command")
    seqs = EventLog(db, "any").sequences()
    assert sorted(map(tuple, seqs)) == [("grep", "read_file"), ("run_command",)]


def test_eventlog_hooks_never_raise(events):
    events._on_pre_tool(calls=None)
    events._on_post_tool(results=[{"bad": "shape"}])
    events._on_post_tool(results=None)


# ── Distiller ────────────────────────────────────────────────


def _populate_repeated_workflow(db, times=3):
    for i in range(times):
        log = EventLog(db, f"sess_{i}")
        log.record("read_file")
        log.record("edit_file_line")
        log.record("run_command")


def test_distill_finds_repeated_workflow(tmp_path, events):
    db = str(tmp_path / "test.db")
    _populate_repeated_workflow(db, times=3)
    saved = []
    d = Distiller(events, lambda n, desc, wf: saved.append((n, desc, wf)), min_support=3)
    report = d.distill()
    assert report["new_skills"], "expected at least one distilled skill"
    name, desc, wf = saved[0]
    assert name.startswith("wf_read_file_edit_file_line")
    assert [s["tool"] for s in wf][:2] == ["read_file", "edit_file_line"]
    assert "observed 3x" in desc


def test_distill_no_duplicates_across_runs(tmp_path, events):
    db = str(tmp_path / "test.db")
    _populate_repeated_workflow(db, times=4)
    saved = []
    d = Distiller(events, lambda n, desc, wf: saved.append(n), min_support=3)
    d.distill()
    first = len(saved)
    assert first > 0
    d.distill()
    assert len(saved) == first  # nothing re-saved


def test_distill_ignores_single_tool_loops_and_low_support(tmp_path):
    db = str(tmp_path / "test.db")
    log = EventLog(db, "s1")
    for _ in range(10):
        log.record("run_command")  # same tool repeated -> not a workflow
    log.record("grep")
    log.record("read_file")  # support 1 < 3
    saved = []
    Distiller(log, lambda n, d, w: saved.append(n), min_support=3).distill()
    assert saved == []


def test_distill_llm_description(tmp_path, events):
    db = str(tmp_path / "test.db")
    _populate_repeated_workflow(db, times=3)
    saved = []
    d = Distiller(events, lambda n, desc, wf: saved.append(desc), llm_fn=lambda p: "Reads, edits, then tests a file.", min_support=3)
    d.distill()
    assert saved and saved[0] == "Reads, edits, then tests a file."


# ── Dreamer ──────────────────────────────────────────────────


def _session(*user_msgs):
    msgs = [{"role": "system", "content": "sys"}]
    for m in user_msgs:
        msgs.append({"role": "user", "content": m})
        msgs.append({"role": "assistant", "content": "ok"})
    return msgs


def test_dream_heuristic_extraction(store):
    report = Dreamer(store).dream([_session("always use python3.14 for tests", "what time is it")], use_llm=False)
    assert report["added"] == 1
    hits = store.search_memories("python3.14 tests")
    assert hits and "python3.14" in hits[0]["content"]


def test_dream_remember_marker_high_importance(store):
    Dreamer(store).dream([_session("remember the API key lives in .env.local")], use_llm=False)
    mems = store.list_memories()
    assert mems[0]["importance"] == 0.8


def test_dream_dedupes_within_and_across_runs(store):
    d = Dreamer(store)
    sessions = [_session("prefer tabs over spaces in this repo")]
    r1 = d.dream(sessions, use_llm=False)
    r2 = d.dream(sessions, use_llm=False)
    assert r1["added"] == 1
    assert r2["added"] == 0 and r2["duplicates"] == 1
    # same content twice in one run also deduped
    r3 = d.dream([_session("never commit directly to main branch"), _session("never commit directly to main branch")], use_llm=False)
    assert r3["added"] == 1


def test_dream_llm_json_path_and_garbage(store):
    good = Dreamer(store, llm_fn=lambda p: 'Sure! [{"content": "build uses vite port 5173", "kind": "project", "importance": 0.9}]')
    report = good.dream([_session("hello")], use_llm=True)
    assert report["added"] == 1
    bad = Dreamer(store, llm_fn=lambda p: "no json here at all")
    report2 = bad.dream([_session("hello again")], use_llm=True)
    assert "added" in report2  # garbage LLM output must not raise


def test_dream_prune_caps_memories(store):
    d = Dreamer(store, max_memories=5)
    for i in range(8):
        store.add_memory(f"unique fact number {i} about module_{i}", "note", 0.1 * (i % 5))
    report = d.dream([], use_llm=False)
    assert report["pruned"] == 3
    assert len(store.list_memories()) == 5


# ── GoalJudge ────────────────────────────────────────────────


def test_judge_fail_open_without_model():
    v = GoalJudge().verdict("ship it", [])
    assert v["complete"] is True and v["confidence"] == 0.0


def test_judge_accepts_and_rejects():
    accept = GoalJudge(llm_fn=lambda p: '{"complete": true, "reason": "tests pass", "confidence": 0.9}')
    reject = GoalJudge(llm_fn=lambda p: 'thinking... {"complete": false, "reason": "no test evidence", "confidence": 0.8}')
    assert accept.verdict("g", [])["complete"] is True
    v = reject.verdict("g", [])
    assert v["complete"] is False and "no test evidence" in v["reason"]


def test_judge_garbage_and_exception_fail_open():
    garbage = GoalJudge(llm_fn=lambda p: "utter nonsense")
    assert garbage.verdict("g", [])["complete"] is True

    def boom(p):
        raise RuntimeError("api down")

    v = GoalJudge(llm_fn=boom).verdict("g", [])
    assert v["complete"] is True and "api down" in v["reason"]


def test_judge_prompt_includes_goal_and_transcript():
    captured = {}

    def spy(prompt):
        captured["prompt"] = prompt
        return '{"complete": true, "reason": "", "confidence": 1.0}'

    GoalJudge(llm_fn=spy).verdict("fix the login bug", [{"role": "tool", "content": "2 passed"}])
    assert "fix the login bug" in captured["prompt"]
    assert "2 passed" in captured["prompt"]


# ── Subconscious ─────────────────────────────────────────────


def _fast_subconscious(store, tmp_path, **kw):
    dreamer = Dreamer(store)
    loader = lambda k: [_session("always run linting before committing")]
    defaults = dict(
        dreamer=dreamer,
        distiller=None,
        session_loader=loader,
        root_dir=str(tmp_path / "memory"),
        idle_seconds=0.05,
        poll_interval=0.02,
        enabled=True,
        use_llm=False,
    )
    defaults.update(kw)
    return Subconscious(**defaults)


def test_subconscious_cycles_when_idle(store, tmp_path):
    sub = _fast_subconscious(store, tmp_path)
    sub.start()
    time.sleep(0.3)
    sub.stop()
    status = sub.status()
    assert status["last_report"] is not None
    assert status["last_report"]["dream"]["added"] == 1
    assert (tmp_path / "memory" / "subconscious.log").exists()


def test_subconscious_one_cycle_per_idle_period(store, tmp_path):
    sub = _fast_subconscious(store, tmp_path)
    sub.start()
    time.sleep(0.3)
    sub.stop()
    assert sub.status()["cycles_last_hour"] == 1  # no re-dream until new activity
    sub.poke()
    assert sub._cycled_since_activity is False  # poke re-arms it


def test_subconscious_disabled_never_cycles(store, tmp_path):
    sub = _fast_subconscious(store, tmp_path, enabled=False)
    sub.start()
    time.sleep(0.2)
    sub.stop()
    assert sub.status()["last_report"] is None


def test_subconscious_env_kill_switch(monkeypatch, store, tmp_path):
    monkeypatch.setenv("HERMES_SUBCONSCIOUS", "off")
    sub = Subconscious(root_dir=str(tmp_path / "memory"))
    assert sub.enabled is False


def test_subconscious_survives_component_errors(tmp_path, store):
    class BoomDreamer:
        def dream(self, *a, **k):
            raise RuntimeError("dream boom")

    class BoomDistiller:
        def distill(self):
            raise RuntimeError("distill boom")

    sub = Subconscious(
        dreamer=BoomDreamer(),
        distiller=BoomDistiller(),
        session_loader=lambda k: [],
        root_dir=str(tmp_path / "memory"),
        enabled=True,
    )
    report = sub.run_cycle()
    assert "dream boom" in report["dream_error"]
    assert "distill boom" in report["distill_error"]
