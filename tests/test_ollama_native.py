"""Ollama-backed providers must use the native /api/chat (num_ctx honored) —
the OpenAI-compatible endpoint silently ignores options.num_ctx (verified
empirically: hermes3:8b loaded at 131k ctx / 23 GB despite the cap)."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.providers import ProviderRouter, _ollama_payload, _ollama_response


def test_payload_has_num_ctx_and_tools(monkeypatch):
    monkeypatch.setenv("HERMES_LOCAL_NUM_CTX", "4096")
    schemas = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    p = _ollama_payload([{"role": "user", "content": "hi"}], schemas, "hermes3:8b")
    assert p["options"]["num_ctx"] == 4096
    assert p["tools"] == schemas and p["model"] == "hermes3:8b"


def test_response_serializes_dict_arguments():
    resp = _ollama_response({"content": "", "tool_calls": [{"function": {"name": "write_file", "arguments": {"filepath": "a.py", "content": "x"}}}]})
    tc = resp.tool_calls[0]
    assert tc.function.name == "write_file"
    assert json.loads(tc.function.arguments) == {"filepath": "a.py", "content": "x"}


def test_call_routes_ollama_to_native_api(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, **kw):
        captured["url"] = url
        captured["payload"] = json
        return SimpleNamespace(
            status_code=200, raise_for_status=lambda: None, json=lambda: {"message": {"content": "native ok"}, "prompt_eval_count": 5, "eval_count": 2}
        )

    monkeypatch.setattr("requests.post", fake_post)
    resp = ProviderRouter.call([{"role": "user", "content": "hi"}], [], "hermes")
    assert resp.content == "native ok"
    assert captured["url"].endswith("/api/chat")  # native, NOT /v1/chat/completions
    assert "num_ctx" in captured["payload"]["options"]
    assert captured["payload"]["stream"] is False


def test_stream_routes_ollama_to_native_api(monkeypatch):
    lines = [
        json.dumps({"message": {"content": "Hel"}, "done": False}).encode(),
        json.dumps({"message": {"content": "lo"}, "done": False}).encode(),
        json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 3, "eval_count": 2}).encode(),
    ]

    def fake_post(url, json=None, timeout=None, stream=False, **kw):
        assert url.endswith("/api/chat") and json["stream"] is True
        return SimpleNamespace(raise_for_status=lambda: None, iter_lines=lambda: iter(lines))

    monkeypatch.setattr("requests.post", fake_post)
    chunks, final = [], None
    for piece, result, usage in ProviderRouter.call_stream([{"role": "user", "content": "hi"}], [], "ollama"):
        if piece:
            chunks.append(piece)
        if result:
            final = result
    assert "".join(chunks) == "Hello"
    assert final.content == "Hello"
