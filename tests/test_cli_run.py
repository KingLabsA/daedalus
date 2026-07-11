"""Tests for `daedalus run` — one-shot headless mode (CI/scripting)."""

import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("HERMES_SUBCONSCIOUS", "off")
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "off")


def _fake_agent(monkeypatch, result="done", raises=None):
    import agent_ultimate as au

    class FakeAgent:
        def __init__(self):
            self.provider = "openai"
            self._provider_pinned = False
            self.logs = []
            self.safety = types.SimpleNamespace(mode="suggest")
            self.subconscious = types.SimpleNamespace(stop=lambda: None)
            self.changesets = types.SimpleNamespace(summary=lambda: {"id": "cs_1", "files": [{"path": "a.py"}]})

        def converse(self, task, max_iters=10):
            if raises:
                raise raises
            self.logs.append({"type": "auto_route", "provider": "hermes", "tier": 0})
            return result

    monkeypatch.setattr(au, "UltimateAgent", FakeAgent)
    return FakeAgent


def test_run_prints_result(monkeypatch, capsys):
    _fake_agent(monkeypatch, result="hello world")
    import hermes_cli

    code = hermes_cli.cmd_run("say hi")
    assert code == 0
    assert "hello world" in capsys.readouterr().out


def test_run_json_output(monkeypatch, capsys):
    _fake_agent(monkeypatch, result="built it")
    import hermes_cli

    code = hermes_cli.cmd_run("build", as_json=True)
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["result"] == "built it"
    assert data["routed_to"] == "hermes"
    assert data["files_changed"] == ["a.py"]


def test_run_provider_pin_and_auto(monkeypatch):
    Fake = _fake_agent(monkeypatch)
    captured = {}
    orig_init = Fake.__init__

    def spy_init(self):
        orig_init(self)
        captured["self"] = self

    monkeypatch.setattr(Fake, "__init__", spy_init)
    import hermes_cli

    hermes_cli.cmd_run("x", provider="deepseek", auto=True)
    a = captured["self"]
    assert a.provider == "deepseek" and a._provider_pinned and a.safety.mode == "auto"


def test_run_error_exit_code(monkeypatch, capsys):
    _fake_agent(monkeypatch, raises=RuntimeError("boom"))
    import hermes_cli

    code = hermes_cli.cmd_run("fail", as_json=True)
    assert code == 1
    assert "boom" in capsys.readouterr().err
