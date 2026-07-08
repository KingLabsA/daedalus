"""Tests for core.observability — structured telemetry + metrics."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.observability import Telemetry


@pytest.fixture
def tel(tmp_path):
    return Telemetry(root_dir=str(tmp_path))


def test_llm_latency_recorded(tel):
    tel._pre_llm()
    time.sleep(0.01)
    tel._post_llm(content="hello", tool_calls=[])
    m = tel.metrics()
    assert m["latency"]["llm_call"]["count"] == 1
    assert m["latency"]["llm_call"]["avg_s"] > 0


def test_tool_latency_and_errors(tel):
    tel._pre_tool(calls=[{"id": "a", "name": "grep", "args": {}},
                         {"id": "b", "name": "run_command", "args": {}}])
    tel._post_tool(results=[{"id": "a", "result": "ok"},
                            {"id": "b", "result": "ToolError: boom"}])
    m = tel.metrics()["latency"]
    assert m["tool:grep"]["count"] == 1 and m["tool:grep"]["errors"] == 0
    assert m["tool:run_command"]["errors"] == 1
    assert m["tool:_all"]["count"] == 2


def test_structured_events_written(tel, tmp_path):
    tel._pre_llm()
    tel._post_llm(content="x", tool_calls=[])
    tel._on_error(error="something broke")
    lines = [json.loads(l) for l in (tmp_path / "telemetry.jsonl").read_text().splitlines()]
    kinds = [l["kind"] for l in lines]
    assert "llm_call" in kinds and "error" in kinds
    assert all("ts" in l for l in lines)
    assert tel.metrics()["counters"]["errors"] == 1


def test_rotation(tel, tmp_path, monkeypatch):
    monkeypatch.setattr("core.observability.MAX_LOG_BYTES", 200)
    for i in range(20):
        tel.event("filler", data="x" * 50)
    assert (tmp_path / "telemetry.jsonl.1").exists()


def test_slowest_tools_and_never_raises(tel):
    tel._pre_tool(calls=[{"id": "s", "name": "slow_tool", "args": {}}])
    time.sleep(0.02)
    tel._post_tool(results=[{"id": "s", "result": "done"}])
    top = tel.slowest_tools()
    assert top and top[0]["tool"] == "slow_tool"
    # garbage inputs are swallowed
    tel._pre_tool(calls=None)
    tel._post_tool(results=[{"bad": "shape"}])
    tel._post_llm()  # no matching pre
