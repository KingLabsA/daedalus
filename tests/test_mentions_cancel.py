"""Tests for @file mentions and mid-run cancellation."""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

import agent_ultimate as au
from agent_ultimate import UltimateAgent, _expand_mentions

# ── @file mentions ───────────────────────────────────────────


def test_expand_mentions_attaches_existing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("SECRET PLAN\n")
    out = _expand_mentions("please read @notes.md and summarize")
    assert "SECRET PLAN" in out
    assert "please read @notes.md" in out  # original text preserved
    assert "```md" in out


def test_expand_mentions_ignores_missing_and_emails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = _expand_mentions("email me@example.com about @nope.txt")
    assert "Attached files" not in out  # no real files -> unchanged
    assert out == "email me@example.com about @nope.txt"


def test_expand_mentions_truncates_large(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 200_000)
    out = _expand_mentions("check @big.txt", max_bytes=1000)
    assert "[...truncated]" in out


def test_expand_mentions_multiple(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("AAA")
    (tmp_path / "b.py").write_text("BBB")
    out = _expand_mentions("compare @a.py and @b.py")
    assert "AAA" in out and "BBB" in out


def test_converse_expands_mentions(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "off")
    monkeypatch.setenv("HERMES_SUBCONSCIOUS", "off")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cfg.txt").write_text("PORT=9999")
    agent = UltimateAgent()
    seen = {}

    def fake_call(messages, schemas, provider=None):
        seen["last_user"] = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        return SimpleNamespace(content="ok", tool_calls=[])

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(fake_call))
    agent.converse("what port? see @cfg.txt")
    assert "PORT=9999" in seen["last_user"]
    agent.subconscious.stop()


# ── cancellation ─────────────────────────────────────────────


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "off")
    monkeypatch.setenv("HERMES_SUBCONSCIOUS", "off")
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="hermes_cancel_")
    os.chdir(tmp)
    a = UltimateAgent()
    yield a
    a.subconscious.stop()
    os.chdir(cwd)


def test_cancel_before_iteration_returns_cancelled(agent, monkeypatch):
    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(lambda m, s, provider=None: SimpleNamespace(content="x", tool_calls=[])))
    agent.cancel_event.set()
    out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "go"}], max_iters=3)
    assert out == "[cancelled]"
    assert not agent.cancel_event.is_set()  # cleared after handling


def test_cancel_stops_mid_stream(agent, monkeypatch):
    emitted = []

    def slow_stream(messages, schemas, provider=None):
        for i in range(100):
            yield f"tok{i} ", None, {}
        yield "", SimpleNamespace(content="done", tool_calls=[]), {}

    monkeypatch.setattr(au.ProviderRouter, "call_stream", staticmethod(slow_stream))

    def on_tok(t):
        emitted.append(t)
        if len(emitted) == 3:
            agent.cancel_event.set()  # user hits cancel after 3 tokens

    agent.on_token = on_tok
    out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "go"}], max_iters=2)
    agent.on_token = None
    assert out == "[cancelled]"
    assert len(emitted) < 100  # stream was aborted, not drained


def test_cancelled_not_retried(agent, monkeypatch):
    calls = {"n": 0}

    def cancel_stream(messages, schemas, provider=None):
        calls["n"] += 1
        agent.cancel_event.set()
        yield "partial ", None, {}

    monkeypatch.setattr(au.ProviderRouter, "call_stream", staticmethod(cancel_stream))
    agent.on_token = lambda t: None
    out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "go"}], max_iters=2)
    agent.on_token = None
    assert out == "[cancelled]"
    assert calls["n"] == 1  # _Cancelled must not trigger the retry
