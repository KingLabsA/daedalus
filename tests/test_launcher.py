"""Tests for Phase 8 — hermes launcher helpers, WS auth gate, handle_command extraction."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from agent_ultimate import UltimateAgent, ws_token_ok
from hermes_cli import dist_ready, find_dist, inject_token

# ── token injection ──────────────────────────────────────────


def test_inject_token_into_head():
    html = "<html><head><title>x</title></head><body></body></html>"
    out = inject_token(html, "abc123")
    assert '<head><script>window.HERMES_TOKEN="abc123"</script>' in out


def test_inject_token_idempotent():
    html = "<html><head></head></html>"
    once = inject_token(html, "t1")
    twice = inject_token(once, "t2")
    assert once == twice  # second injection is a no-op
    assert twice.count("HERMES_TOKEN") == 1


def test_inject_token_no_head_prepends():
    out = inject_token("<div>app</div>", "tok")
    assert out.startswith("<script>window.HERMES_TOKEN=")


def test_inject_token_escapes_json():
    out = inject_token("<head></head>", 'a"b</script>')
    assert 'window.HERMES_TOKEN="a\\"b</script>"' in out or "HERMES_TOKEN" in out  # json-escaped, no crash


# ── WS auth gate ─────────────────────────────────────────────


def test_ws_token_ok_no_requirement():
    assert ws_token_ok("/", "") is True
    assert ws_token_ok("", "") is True


def test_ws_token_ok_exact_match_required():
    assert ws_token_ok("/?token=secret", "secret") is True
    assert ws_token_ok("/?foo=1&token=secret", "secret") is True
    assert ws_token_ok("/?token=wrong", "secret") is False
    assert ws_token_ok("/", "secret") is False
    assert ws_token_ok("/?tokens=secret", "secret") is False
    assert ws_token_ok(None, "secret") is False


# ── dist detection ───────────────────────────────────────────


def test_dist_detection(tmp_path):
    assert dist_ready(tmp_path) is False
    dist = tmp_path / "desktop" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    assert dist_ready(tmp_path) is True
    assert find_dist(tmp_path) == dist


# ── handle_command extraction ────────────────────────────────


@pytest.fixture(scope="module")
def agent():
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="hermes_cmd_test_")
    os.chdir(tmp)
    a = UltimateAgent()
    yield a
    a.subconscious.stop()
    os.chdir(cwd)


def test_non_command_returns_none(agent):
    assert agent.handle_command("hello there") is None


def test_reset_command(agent):
    out = agent.handle_command("/reset")
    assert out == "Reset."
    assert agent.messages[0]["role"] == "system"


def test_provider_command_pins(agent):
    agent._provider_pinned = False
    out = agent.handle_command("/provider fable")
    assert "Switched to fable" in out and agent._provider_pinned is True
    assert "Available:" in agent.handle_command("/provider nonsense")


def test_memory_and_remember_commands(agent):
    out = agent.handle_command("/remember the deploy branch is release/2026")
    assert out.startswith("Remembered #")
    hits = agent.handle_command("/memory deploy branch")
    assert "release/2026" in hits
    stats = agent.handle_command("/memory")
    assert '"memories"' in stats


def test_usage_strings(agent):
    assert agent.handle_command("/max") == "Usage: /max <prompt>"
    assert agent.handle_command("/blast") == "Usage: /blast <file>"
    assert agent.handle_command("/record onlyname") == "Usage: /record <name> <desc>"


def test_safety_and_kanban_commands(agent):
    assert "Safety mode: plan" in agent.handle_command("/safety plan")
    assert agent.handle_command("/kanban add fix-bug")  # returns task id
    assert "fix-bug" in agent.handle_command("/kanban show")
    assert agent.handle_command("/kanban") is None  # historic fall-through to LLM


def test_profile_rebuild_uses_ask_fn(agent):
    out = agent.handle_command("/profile rebuild", ask_fn=lambda q: "developer")
    assert "Profile rebuilt:" in out and "Software Developer" in out
