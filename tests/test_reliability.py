"""Tests for Phase 9 — provider liveness, local guardrails, retry/failover, streaming."""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

import agent_ultimate as au
from agent_ultimate import (
    CORE_TOOLS, PROVIDER_CONFIGS, UltimateAgent, _model_for, _openai_call_kwargs,
    _probe_provider, _provider_alive, _LIVE_CACHE,
)


@pytest.fixture(scope="module")
def agent():
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="hermes_rel_test_")
    os.chdir(tmp)
    a = UltimateAgent()
    yield a
    a.subconscious.stop()
    os.chdir(cwd)


@pytest.fixture(autouse=True)
def no_auto_route(monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "off")


def _fake_response(content="done", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


# ── provider config ──────────────────────────────────────────

def test_freellmapi_provider_exists():
    cfg = PROVIDER_CONFIGS["freellmapi"]
    assert cfg["local"] is True
    assert "3002" in cfg["base"]
    assert cfg["env"] == "FREELLMAPI_API_KEY"


def test_local_flags():
    assert PROVIDER_CONFIGS["ollama"]["local"] and PROVIDER_CONFIGS["ollama"]["ollama"]
    assert PROVIDER_CONFIGS["hermes"]["local"]
    assert "local" not in PROVIDER_CONFIGS["openai"]


def test_model_override_scoped_to_selected_provider(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "forced-model")
    selected = au.LLM_PROVIDER
    assert _model_for(selected, PROVIDER_CONFIGS[selected]) == "forced-model"
    other = "ollama" if selected != "ollama" else "hermes"
    assert _model_for(other, PROVIDER_CONFIGS[other]) == PROVIDER_CONFIGS[other]["default_model"]


def test_openai_call_kwargs():
    cloud = _openai_call_kwargs(PROVIDER_CONFIGS["openai"])
    assert cloud["timeout"] == 120.0 and "extra_body" not in cloud
    local = _openai_call_kwargs(PROVIDER_CONFIGS["hermes"])
    assert local["extra_body"]["options"]["num_ctx"] == 8192


# ── liveness ─────────────────────────────────────────────────

def test_probe_local_provider(monkeypatch):
    calls = {}

    def fake_get(url, **kw):
        calls["url"] = url
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("requests.get", fake_get)
    assert _probe_provider("ollama") is True
    assert "/v1/models" in calls["url"]


def test_probe_cloud_requires_valid_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "bad-key")
    monkeypatch.setattr("requests.get", lambda url, **kw: SimpleNamespace(status_code=401))
    assert _probe_provider("groq") is False  # 401 -> key invalid -> NOT live
    monkeypatch.setattr("requests.get", lambda url, **kw: SimpleNamespace(status_code=200))
    assert _probe_provider("groq") is True
    monkeypatch.delenv("GROQ_API_KEY")
    assert _probe_provider("groq") is False  # no key at all


def test_provider_alive_caches(monkeypatch):
    _LIVE_CACHE.clear()
    count = {"n": 0}

    def fake_get(url, **kw):
        count["n"] += 1
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr("requests.get", fake_get)
    assert _provider_alive("ollama") is True
    assert _provider_alive("ollama") is True
    assert count["n"] == 1  # second call served from cache
    _LIVE_CACHE.clear()


# ── local guardrails in run_loop ─────────────────────────────

def test_local_provider_gets_pruned_tools(agent, monkeypatch):
    captured = {}

    def fake_call(messages, schemas, provider=None):
        captured["schemas"] = schemas
        captured["provider"] = provider
        return _fake_response("hi")

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(fake_call))
    agent.provider = "hermes"  # local
    agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "hello"}], max_iters=1)
    names = {s["function"]["name"] for s in captured["schemas"]}
    assert names <= set(CORE_TOOLS)
    assert len(names) <= len(CORE_TOOLS)

    agent.provider = "openai"  # cloud gets everything
    agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "hello"}], max_iters=1)
    assert len(captured["schemas"]) > 50


# ── retry + failover ─────────────────────────────────────────

def test_retry_once_then_success(agent, monkeypatch):
    attempts = {"n": 0}

    def flaky(messages, schemas, provider=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient blip")
        return _fake_response("recovered")

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(flaky))
    monkeypatch.setattr(au.time, "sleep", lambda s: None)
    agent.provider = "openai"
    out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "go"}], max_iters=1)
    assert out == "recovered" and attempts["n"] == 2


def test_failover_to_next_live_provider(agent, monkeypatch):
    def dead_then_alive(messages, schemas, provider=None):
        if provider == "openai":
            raise RuntimeError("provider down")
        return _fake_response(f"answered-by-{provider}")

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(dead_then_alive))
    monkeypatch.setattr(au.time, "sleep", lambda s: None)
    monkeypatch.setattr(au, "_live_providers", lambda: ["openai", "groq"])
    agent.provider = "openai"
    out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "go"}], max_iters=3)
    assert out == "answered-by-groq"
    assert any(l["type"] == "provider_failover" for l in agent.logs)


def test_failover_does_not_burn_iterations(agent, monkeypatch):
    # with max_iters=1, a dead first provider must still produce an answer
    def dead_then_alive(messages, schemas, provider=None):
        if provider == "openai":
            raise RuntimeError("down")
        return _fake_response(f"answered-by-{provider}")

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(dead_then_alive))
    monkeypatch.setattr(au.time, "sleep", lambda s: None)
    monkeypatch.setattr(au, "_live_providers", lambda: ["openai", "groq"])
    agent.provider = "openai"
    out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "go"}], max_iters=1)
    assert out == "answered-by-groq"


def test_local_gateway_needs_key_to_be_live(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_API_KEY", raising=False)
    monkeypatch.setattr("requests.get", lambda url, **kw: SimpleNamespace(status_code=200))
    assert _probe_provider("freellmapi") is False  # endpoint up but key unset -> not usable
    monkeypatch.setenv("FREELLMAPI_API_KEY", "freellmapi-x")
    assert _probe_provider("freellmapi") is True


def test_all_providers_dead_returns_error(agent, monkeypatch):
    def always_dead(messages, schemas, provider=None):
        raise RuntimeError("everything down")

    monkeypatch.setattr(au.ProviderRouter, "call", staticmethod(always_dead))
    monkeypatch.setattr(au.time, "sleep", lambda s: None)
    monkeypatch.setattr(au, "_live_providers", lambda: [])
    agent.provider = "openai"
    out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "go"}], max_iters=2)
    assert out.startswith("LLM Error:")


# ── streaming ────────────────────────────────────────────────

def test_on_token_streams_and_assembles(agent, monkeypatch):
    def fake_stream(messages, schemas, provider=None):
        yield "Hel", None, {}
        yield "lo!", None, {}
        yield "", SimpleNamespace(content="", tool_calls=[]), {}

    monkeypatch.setattr(au.ProviderRouter, "call_stream", staticmethod(fake_stream))
    tokens = []
    agent.on_token = tokens.append
    try:
        agent.provider = "openai"
        out = agent.run_loop([{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}], max_iters=1)
    finally:
        agent.on_token = None
    assert tokens == ["Hel", "lo!"]
    assert out == "Hello!"


def test_opencode_provider_config():
    from core.providers import PROVIDER_CONFIGS
    c = PROVIDER_CONFIGS["opencode"]
    assert c["env"] == "OPENCODE_API_KEY"
    assert "opencode.ai" in c["base"] and c["base"].endswith("/v1")
    assert c["lib"] == "openai"  # OpenAI-compatible gateway
    from core.epistemic.router import PROVIDER_TIERS
    assert PROVIDER_TIERS["opencode"] == 4  # strong tier
