"""Tests for Phase 10 — multi-turn continuity, diff-approve, destructive detection."""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

import agent_ultimate as au
from agent_ultimate import UltimateAgent, _is_destructive, _unified_diff


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "off")
    monkeypatch.setenv("HERMES_SUBCONSCIOUS", "off")
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="hermes_convo_test_")
    os.chdir(tmp)
    a = UltimateAgent()
    yield a
    a.subconscious.stop()
    os.chdir(cwd)


def _resp(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


def _toolcall(cid, name, args):
    import json

    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


# ── destructive detection + diff ─────────────────────────────


def test_is_destructive():
    assert _is_destructive("write_file") and _is_destructive("run_command")
    assert not _is_destructive("read_file") and not _is_destructive("grep")


def test_unified_diff():
    d = _unified_diff("line1\nline2\n", "line1\nCHANGED\n", "f.py")
    assert "-line2" in d and "+CHANGED" in d


# ── multi-turn continuity ────────────────────────────────────


def test_converse_accumulates_turns(agent, monkeypatch):
    seen = {"lens": []}

    def fake_call(messages, schemas, provider=None):
        seen["lens"].append(len([m for m in messages if m["role"] == "user"]))
        return _resp("ok")

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(fake_call))
    agent.converse("first question")
    agent.converse("second question")
    # second turn's context must include BOTH user messages -> continuity
    assert seen["lens"][-1] >= 2
    user_msgs = [m for m in agent.convo if m["role"] == "user"]
    assert any("first question" in m["content"] for m in user_msgs)
    assert any("second question" in m["content"] for m in user_msgs)


def test_converse_starts_with_system(agent, monkeypatch):
    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(lambda m, s, provider=None: _resp("hi")))
    agent.converse("hello")
    assert agent.convo[0]["role"] == "system"


def test_reset_clears_convo(agent, monkeypatch):
    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(lambda m, s, provider=None: _resp("hi")))
    agent.converse("hello")
    assert len(agent.convo) > 1
    agent.handle_command("/reset")
    assert agent.convo == []


# ── diff-approve flow ────────────────────────────────────────


def test_approve_fn_gates_writes(agent, monkeypatch):
    agent.safety.mode = "suggest"  # writes blocked unless approved
    calls = {"n": 0}

    def fake_call(messages, schemas, provider=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp("", [_toolcall("c1", "write_file", {"filepath": "out.txt", "content": "hello"})])
        return _resp("done")

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(fake_call))
    previews = []
    agent.approve_fn = lambda tool, args, preview: previews.append((tool, preview)) or True
    agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "write a file"}], max_iters=3)
    assert previews and previews[0][0] == "write_file"
    assert Path("out.txt").exists() and Path("out.txt").read_text() == "hello"


def test_approve_fn_denial_feeds_back(agent, monkeypatch):
    agent.safety.mode = "suggest"
    calls = {"n": 0}

    def fake_call(messages, schemas, provider=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp("", [_toolcall("c1", "write_file", {"filepath": "no.txt", "content": "x"})])
        # after denial, a follow-up user message must be present
        assert any("[USER DENIED]" in str(m.get("content", "")) for m in messages)
        return _resp("understood, I'll do something else")

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(fake_call))
    agent.approve_fn = lambda tool, args, preview: False  # deny
    out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "write no.txt"}], max_iters=3)
    assert not Path("no.txt").exists()
    assert "something else" in out


def test_preview_write_renders_diff(agent):
    Path("existing.txt").write_text("old content\n")
    preview = agent._preview_write("write_file", {"filepath": "existing.txt", "content": "new content\n"})
    assert "-old content" in preview and "+new content" in preview
    cmd_preview = agent._preview_write("run_command", {"command": "rm -rf build"})
    assert "rm -rf build" in cmd_preview
