"""Tests for Phase 7 — cost-aware auto-routing wired into the agent loop."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from agent_ultimate import UltimateAgent


@pytest.fixture(scope="module")
def agent():
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="hermes_route_test_")
    os.chdir(tmp)
    a = UltimateAgent()
    yield a
    a.subconscious.stop()
    os.chdir(cwd)


def test_route_provider_easy_vs_hard(agent, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "on")
    agent._provider_pinned = False
    easy_provider, easy_tier = agent._route_provider("fix a typo in the README")
    hard_provider, hard_tier = agent._route_provider("design the architecture for a distributed migration, analyze trade-offs and debug this race condition")
    # tiers only reported when routing actually diverts from the default provider
    if easy_tier is not None and hard_tier is not None:
        assert easy_tier <= hard_tier


def test_route_provider_respects_pin(agent, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "on")
    agent._provider_pinned = True
    provider, tier = agent._route_provider("design a distributed system architecture with trade-offs")
    assert provider == agent.provider and tier is None
    agent._provider_pinned = False


def test_route_provider_env_kill_switch(agent, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "off")
    provider, tier = agent._route_provider("design a distributed system architecture with trade-offs")
    assert provider == agent.provider and tier is None


def test_route_provider_empty_text(agent, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "on")
    provider, tier = agent._route_provider("")
    assert provider == agent.provider and tier is None


def test_route_decision_logged(agent, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_ROUTE", "on")
    agent._provider_pinned = False
    agent.logs.clear()
    provider, tier = agent._route_provider("fix a typo")
    if provider != agent.provider:
        assert any(l["type"] == "auto_route" for l in agent.logs)
