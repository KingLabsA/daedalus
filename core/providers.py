"""Provider layer — configs, clients, routing, cost tracking, validated liveness.

Extracted from the agent monolith (modularization slice 1). Standalone: imports
nothing from agent_ultimate; the agent re-exports these names for back-compat.
"""

import importlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")

# Cost tracking
COST_PER_1K: dict[str, dict[str, float]] = {
    "openai": {"input": 0.00025, "output": 0.001},
    "anthropic": {"input": 0.003, "output": 0.015},
    "groq": {"input": 0.0001, "output": 0.0001},
    "mistral": {"input": 0.0002, "output": 0.0006},
    "google": {"input": 0.000125, "output": 0.0005},
    "deepseek": {"input": 0.00014, "output": 0.00028},
    "together": {"input": 0.0001, "output": 0.0001},
    "fireworks": {"input": 0.0001, "output": 0.0001},
    "xai": {"input": 0.00015, "output": 0.0006},
    "perplexity": {"input": 0.0003, "output": 0.0015},
    "novita": {"input": 0.0001, "output": 0.0002},
    "openrouter": {"input": 0.0001, "output": 0.0001},
    "zhipu": {"input": 0.0001, "output": 0.0001},
    "moonshot": {"input": 0.0001, "output": 0.0001},
    "cohere": {"input": 0.00015, "output": 0.0006},
    "ollama": {"input": 0, "output": 0},
    "bedrock": {"input": 0.0008, "output": 0.0032},
    "azure": {"input": 0.00025, "output": 0.001},
    "huggingface": {"input": 0.0001, "output": 0.0001},
    "replicate": {"input": 0.0001, "output": 0.0001},
}

session_costs: list[dict] = []

# Config file loader
HERMES_CONFIG_PATH = ".hermes.json"


def _load_config() -> dict:
    path = Path(HERMES_CONFIG_PATH)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_config(cfg: dict):
    Path(HERMES_CONFIG_PATH).write_text(json.dumps(cfg, indent=2))


def _track_cost(provider: str, in_tokens: int, out_tokens: int):
    rates = COST_PER_1K.get(provider, {"input": 0.0005, "output": 0.0015})
    cost = (in_tokens / 1000) * rates["input"] + (out_tokens / 1000) * rates["output"]
    session_costs.append({"provider": provider, "input": in_tokens, "output": out_tokens, "cost": round(cost, 6), "ts": datetime.now().isoformat()})


def _get_cost_summary() -> dict:
    total_cost = sum(c["cost"] for c in session_costs)
    total_in = sum(c["input"] for c in session_costs)
    total_out = sum(c["output"] for c in session_costs)
    by_provider = {}
    for c in session_costs:
        p = c["provider"]
        if p not in by_provider:
            by_provider[p] = {"calls": 0, "input": 0, "output": 0, "cost": 0}
        by_provider[p]["calls"] += 1
        by_provider[p]["input"] += c["input"]
        by_provider[p]["output"] += c["output"]
        by_provider[p]["cost"] += c["cost"]
    return {
        "total_cost": round(total_cost, 4),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "session_calls": len(session_costs),
        "by_provider": by_provider,
    }


# ============== PROVIDERS ==============
PROVIDER_CONFIGS = {
    "openai": {"env": "OPENAI_API_KEY", "lib": "openai", "client": "OpenAI", "default_model": "gpt-4o-mini"},
    "anthropic": {
        "env": "ANTHROPIC_API_KEY",
        "lib": "anthropic",
        "client": "Anthropic",
        "default_model": "claude-3-5-sonnet-20241022",
        "models": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-3-5-sonnet-20241022"],
    },
    "fable": {
        "env": "ANTHROPIC_API_KEY",
        "lib": "anthropic",
        "client": "Anthropic",
        "default_model": "claude-fable-5",
        "description": "Claude Fable 5 — Anthropic's Mythos-class frontier model",
        "models": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
    },
    "openrouter": {"env": "OPENROUTER_API_KEY", "lib": "openai", "base": "https://openrouter.ai/api/v1", "default_model": "openai/gpt-4o-mini"},
    "ollama": {
        "env": "",
        "lib": "openai",
        "base": os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
        "default_model": os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b"),
        "local": True,
        "ollama": True,
    },
    "hermes": {
        "env": "",
        "lib": "openai",
        "base": os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
        "default_model": "hermes3:8b",
        "description": "Nous Hermes 3 via Ollama — uncensored, tool-use, reasoning",
        "models": ["hermes3:3b", "hermes3:8b", "hermes3:70b", "hermes3:405b"],
        "local": True,
        "ollama": True,
    },
    "freellmapi": {
        "env": "FREELLMAPI_API_KEY",
        "lib": "openai",
        "base": os.getenv("FREELLMAPI_HOST", "http://localhost:3002") + "/v1",
        "default_model": os.getenv("FREELLMAPI_MODEL", "auto"),
        "description": "Local FreeLLMAPI gateway (67 free models) — launch it first",
        "local": True,
    },
    "opencode": {
        "env": "OPENCODE_API_KEY",
        "lib": "openai",
        "base": os.getenv("OPENCODE_HOST", "https://opencode.ai/zen") + "/v1",
        "default_model": os.getenv("OPENCODE_MODEL", "claude-sonnet-4"),
        "description": "OpenCode Zen gateway — frontier models via one key (opencode.ai/zen)",
    },
    "google": {"env": "GOOGLE_API_KEY", "lib": "google.generativeai", "default_model": "gemini-1.5-pro"},
    "groq": {"env": "GROQ_API_KEY", "lib": "openai", "base": "https://api.groq.com/openai/v1", "default_model": "llama3-70b-8192"},
    "xai": {"env": "XAI_API_KEY", "lib": "openai", "base": "https://api.x.ai/v1", "default_model": "grok-2-1212"},
    "deepseek": {"env": "DEEPSEEK_API_KEY", "lib": "openai", "base": "https://api.deepseek.com", "default_model": "deepseek-chat"},
    "zhipu": {"env": "ZHIPU_API_KEY", "lib": "openai", "base": "https://open.bigmodel.cn/api/paas/v4", "default_model": "glm-4-plus"},
    "moonshot": {"env": "MOONSHOT_API_KEY", "lib": "openai", "base": "https://api.moonshot.cn/v1", "default_model": "moonshot-v1-8k"},
    "mistral": {"env": "MISTRAL_API_KEY", "lib": "openai", "base": "https://api.mistral.ai/v1", "default_model": "mistral-large-latest"},
    "together": {"env": "TOGETHER_API_KEY", "lib": "openai", "base": "https://api.together.xyz/v1", "default_model": "mistralai/Mixtral-8x7B-Instruct-v0.1"},
    "fireworks": {
        "env": "FIREWORKS_API_KEY",
        "lib": "openai",
        "base": "https://api.fireworks.ai/inference/v1",
        "default_model": "accounts/fireworks/models/llama-v3p1-70b-instruct",
    },
    "cohere": {"env": "COHERE_API_KEY", "lib": "cohere", "default_model": "command-r-plus"},
    "perplexity": {"env": "PERPLEXITY_API_KEY", "lib": "openai", "base": "https://api.perplexity.ai", "default_model": "sonar-pro"},
    "novita": {"env": "NOVITA_API_KEY", "lib": "openai", "base": "https://api.novita.ai/v3/openai", "default_model": "meta-llama/llama-3.1-8b-instruct"},
    "bedrock": {"env": "AWS_ACCESS_KEY_ID", "lib": "openai", "base": None, "default_model": "anthropic.claude-3-5-sonnet-20241022-v2:0"},
    "azure": {"env": "AZURE_OPENAI_API_KEY", "lib": "openai", "base": None, "default_model": "gpt-4o-mini"},
    "huggingface": {
        "env": "HF_TOKEN",
        "lib": "openai",
        "base": "https://api-inference.huggingface.co/v1",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
    },
    "replicate": {"env": "REPLICATE_API_TOKEN", "lib": "openai", "base": "https://api.replicate.com/v1", "default_model": "meta/meta-llama-3.1-70b-instruct"},
}


def _get_provider_client(provider: str = None):
    provider = provider or LLM_PROVIDER
    cfg = PROVIDER_CONFIGS.get(provider)
    if not cfg:
        raise ValueError(f"Unknown provider: {provider}")
    lib = cfg.get("lib")
    mod = importlib.import_module(lib)
    client_class_name = cfg.get("client", "OpenAI")
    ClientClass = getattr(mod, client_class_name)
    api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
    base = cfg.get("base")
    if base and not api_key:
        api_key = "ollama"  # Ollama doesn't need a real key
    if base:
        return ClientClass(api_key=api_key, base_url=base)
    return ClientClass(api_key=api_key) if api_key else ClientClass()


def _model_for(provider: str, cfg: dict) -> str:
    """MODEL_NAME env only overrides the user's selected provider; routed
    providers always use their own default (a global override breaks routing)."""
    if provider == LLM_PROVIDER and os.getenv("MODEL_NAME"):
        return os.getenv("MODEL_NAME")
    return cfg.get("default_model", "gpt-4o-mini")


def _openai_call_kwargs(cfg: dict) -> dict:
    """Timeout for every OpenAI-compatible call; num_ctx cap for Ollama-backed
    models so an 8B model doesn't balloon to a 131k-token 22 GB allocation."""
    kwargs = {"timeout": float(os.getenv("HERMES_LLM_TIMEOUT", "120"))}
    if cfg.get("ollama"):
        kwargs["extra_body"] = {"options": {"num_ctx": int(os.getenv("HERMES_LOCAL_NUM_CTX", "8192"))}}
    return kwargs


def _ollama_payload(messages: list[dict], tools_schemas: list[dict], model: str) -> dict:
    om = []
    for m in messages:
        role = m.get("role")
        if role in ("system", "user", "assistant", "tool"):
            om.append({"role": role, "content": str(m.get("content") or "")})
    payload: dict[str, Any] = {
        "model": model,
        "messages": om,
        "options": {"num_ctx": int(os.getenv("HERMES_LOCAL_NUM_CTX", "8192"))},
    }
    if tools_schemas:
        payload["tools"] = tools_schemas
    return payload


def _ollama_response(msg: dict):
    tool_calls = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function", {})
        args = fn.get("arguments")
        f_obj = type("F", (), {"name": fn.get("name", ""), "arguments": args if isinstance(args, str) else json.dumps(args or {})})()
        tool_calls.append(type("TC", (), {"id": f"ollama_call_{i}", "function": f_obj})())
    return type("Response", (), {"content": msg.get("content", "") or "", "tool_calls": tool_calls})()


def _ollama_native_call(cfg: dict, provider: str, model: str, messages, tools_schemas):
    """Ollama's OpenAI-compatible endpoint silently IGNORES options.num_ctx, so a
    model whose Modelfile sets a huge context (e.g. 131k -> 23 GB) loads at full
    size and swamps the machine. The native /api/chat honors it — use that."""
    import requests as _rq

    host = cfg.get("base", "").rsplit("/v1", 1)[0]
    payload = _ollama_payload(messages, tools_schemas, model)
    payload["stream"] = False
    r = _rq.post(f"{host}/api/chat", json=payload, timeout=float(os.getenv("HERMES_LLM_TIMEOUT", "120")))
    r.raise_for_status()
    data = r.json()
    _track_cost(provider, data.get("prompt_eval_count", 0), data.get("eval_count", 0))
    return _ollama_response(data.get("message", {}))


def _ollama_native_stream(cfg: dict, provider: str, model: str, messages, tools_schemas):
    # Streaming + tools is unreliable on Ollama (<0.4.6 emits the tool call as
    # raw text and stalls the stream mid-call). Tool-turns go non-streaming —
    # every Ollama version then returns structured tool_calls — and the content
    # is emitted as one chunk so the UI still updates.
    if tools_schemas:
        resp = _ollama_native_call(cfg, provider, model, messages, tools_schemas)
        if resp.content:
            yield resp.content, None, {}
        yield "", resp, {}
        return
    import requests as _rq

    host = cfg.get("base", "").rsplit("/v1", 1)[0]
    payload = _ollama_payload(messages, tools_schemas, model)
    payload["stream"] = True
    r = _rq.post(f"{host}/api/chat", json=payload, stream=True, timeout=float(os.getenv("HERMES_LLM_TIMEOUT", "120")))
    r.raise_for_status()
    content_parts: list[str] = []
    tool_calls: list[dict] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for line in r.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        msg = chunk.get("message", {})
        piece = msg.get("content", "")
        if piece:
            content_parts.append(piece)
            yield piece, None, {}
        if msg.get("tool_calls"):
            tool_calls.extend(msg["tool_calls"])
        if chunk.get("done"):
            usage = {"prompt_tokens": chunk.get("prompt_eval_count", 0), "completion_tokens": chunk.get("eval_count", 0)}
    _track_cost(provider, usage["prompt_tokens"], usage["completion_tokens"])
    final = _ollama_response({"content": "".join(content_parts), "tool_calls": tool_calls})
    yield "", final, usage


class ProviderRouter:
    @staticmethod
    def call(messages: list[dict], tools_schemas: list[dict], provider: str = None):
        """Non-streaming call. Returns response object with .content and .tool_calls."""
        provider = provider or LLM_PROVIDER
        cfg = PROVIDER_CONFIGS.get(provider)
        if not cfg:
            raise ValueError(f"Unsupported provider: {provider}")
        model = _model_for(provider, cfg)
        api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
        base = cfg.get("base")

        if cfg.get("ollama"):
            return _ollama_native_call(cfg, provider, model, messages, tools_schemas)

        if cfg.get("lib") == "openai":
            import openai

            if base and not api_key:
                api_key = "ollama"  # Ollama doesn't need a real key
            client = openai.OpenAI(api_key=api_key, base_url=base) if api_key or base else openai.OpenAI()
            om = []
            for m in messages:
                if m["role"] == "system":
                    om.append({"role": "system", "content": m["content"]})
                elif m["role"] == "user":
                    om.append({"role": "user", "content": m.get("content", "")})
                elif m["role"] == "assistant":
                    om.append({"role": "assistant", "content": m.get("content", "")})
                elif m["role"] == "tool":
                    om.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "content": m["content"]})
            resp = client.chat.completions.create(
                model=model,
                messages=om,
                tools=tools_schemas if tools_schemas else None,
                tool_choice="auto" if tools_schemas else None,
                **_openai_call_kwargs(cfg),
            )
            _track_cost(provider, resp.usage.prompt_tokens, resp.usage.completion_tokens)
            return resp.choices[0].message

        elif cfg.get("lib") == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            cm = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ["user", "assistant"]]
            an_tools = [
                {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tools_schemas
            ]
            resp = client.messages.create(model=model, system=system, messages=cm, tools=an_tools, max_tokens=4096)
            _track_cost(provider, resp.usage.input_tokens, resp.usage.output_tokens)
            # Convert to OpenAI-like response
            content_text = ""
            tool_calls = []
            for block in resp.content:
                if hasattr(block, "type") and block.type == "text":
                    content_text += block.text
                elif hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append(
                        type("ToolCall", (), {"id": block.id, "function": type("Function", (), {"name": block.name, "arguments": json.dumps(block.input)})()})()
                    )
            result = type("Response", (), {"content": content_text, "tool_calls": tool_calls})()
            return result

        elif cfg.get("lib") == "google.generativeai":
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            gen_model = genai.GenerativeModel(model)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            history = []
            for m in messages:
                if m["role"] == "system":
                    continue
                history.append({"role": "model" if m["role"] == "assistant" else "user", "parts": [m.get("content", "")]})
            chat = gen_model.start_chat(history=history[:-1] if history else [])
            last_msg = history[-1]["parts"][0] if history else "Hello"
            resp = chat.send_message(last_msg)
            text = resp.text if hasattr(resp, "text") else ""
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            if hasattr(resp, "usage_metadata"):
                usage = {
                    "prompt_tokens": getattr(resp.usage_metadata, "prompt_token_count", 0),
                    "completion_tokens": getattr(resp.usage_metadata, "candidates_token_count", 0),
                }
            _track_cost(provider, usage["prompt_tokens"], usage["completion_tokens"])
            return type("Response", (), {"content": text, "tool_calls": []})()

        else:
            # Generic OpenAI-compatible fallback
            try:
                import openai

                if base and not api_key:
                    api_key = "ollama"
                client = openai.OpenAI(api_key=api_key, base_url=base) if api_key or base else openai.OpenAI()
                om = [{"role": m["role"], "content": m.get("content", "")} for m in messages if m["role"] in ("system", "user", "assistant")]
                resp = client.chat.completions.create(
                    model=model,
                    messages=om,
                    tools=tools_schemas if tools_schemas else None,
                    tool_choice="auto" if tools_schemas else None,
                    **_openai_call_kwargs(cfg),
                )
                _track_cost(provider, resp.usage.prompt_tokens, resp.usage.completion_tokens)
                return resp.choices[0].message
            except Exception as e:
                raise ValueError(f"Provider {provider} failed: {e}")

    @staticmethod
    def call_stream(messages: list[dict], tools_schemas: list[dict], provider: str = None):
        """Streaming variant: yields (chunk_text, tool_calls_or_None, usage_dict)."""
        provider = provider or LLM_PROVIDER
        cfg = PROVIDER_CONFIGS.get(provider)
        if not cfg:
            raise ValueError(f"Unsupported provider: {provider}")
        model = _model_for(provider, cfg)
        api_key = os.getenv(cfg["env"]) if cfg.get("env") else None
        base = cfg.get("base")

        if cfg.get("ollama"):
            yield from _ollama_native_stream(cfg, provider, model, messages, tools_schemas)
            return

        if cfg.get("lib") == "openai":
            import openai

            if base and not api_key:
                api_key = "ollama"  # Ollama doesn't need a real key
            client = openai.OpenAI(api_key=api_key, base_url=base) if api_key or base else openai.OpenAI()
            om = []
            for m in messages:
                if m["role"] == "system":
                    om.append({"role": "system", "content": m["content"]})
                elif m["role"] == "user":
                    om.append({"role": "user", "content": m.get("content", "")})
                elif m["role"] == "assistant":
                    om.append({"role": "assistant", "content": m.get("content", "")})
                elif m["role"] == "tool":
                    om.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "content": m["content"]})
            stream = client.chat.completions.create(
                model=model,
                messages=om,
                tools=tools_schemas if tools_schemas else None,
                tool_choice="auto" if tools_schemas else None,
                stream=True,
                **_openai_call_kwargs(cfg),
            )
            tool_calls = []
            content_parts = []
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta:
                    if delta.content:
                        content_parts.append(delta.content)
                        yield delta.content, None, None
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            while len(tool_calls) <= idx:
                                tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                            if tc.id:
                                tool_calls[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls[idx]["function"]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls[idx]["function"]["arguments"] += tc.function.arguments
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = {"prompt_tokens": chunk.usage.prompt_tokens, "completion_tokens": chunk.usage.completion_tokens}
            _track_cost(provider, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

            class StreamResult:
                def __init__(self):
                    self.content = "".join(content_parts) or None
                    self.tool_calls = tool_calls if tool_calls and tool_calls[0]["id"] else []
                    self.usage = type("U", (), usage)()

            yield None, StreamResult(), usage

        elif cfg.get("lib") == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            cm = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ["user", "assistant"]]
            an_tools = [
                {"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tools_schemas
            ]
            content_parts = []
            tool_calls = []
            with client.messages.stream(model=model, system=system, messages=cm, tools=an_tools, max_tokens=4096) as stream:
                for text in stream.text_stream:
                    content_parts.append(text)
                    yield text, None, None
                resp = stream.get_final_message()
            for block in resp.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append({"id": block.id, "function": {"name": block.name, "arguments": json.dumps(block.input)}})
            _track_cost(provider, resp.usage.input_tokens, resp.usage.output_tokens)

            class StreamResult:
                def __init__(self):
                    self.content = "".join(content_parts) or None
                    self.tool_calls = tool_calls
                    self.usage = type("U", (), {"prompt_tokens": resp.usage.input_tokens, "completion_tokens": resp.usage.output_tokens})()

            yield None, StreamResult(), {}

        elif cfg.get("lib") == "google.generativeai":
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            gen_model = genai.GenerativeModel(model)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            history = []
            for m in messages:
                if m["role"] == "system":
                    continue
                history.append({"role": "model" if m["role"] == "assistant" else "user", "parts": [m.get("content", "")]})
            chat = gen_model.start_chat(history=history[:-1] if history else [])
            last_msg = history[-1]["parts"][0] if history else "Hello"
            content_parts = []
            tool_calls = []
            for chunk in chat.send_message_streaming(last_msg):
                if hasattr(chunk, "text") and chunk.text:
                    content_parts.append(chunk.text)
                    yield chunk.text, None, None
            resp = chat.last if hasattr(chat, "last") else None
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            if resp and hasattr(resp, "usage_metadata"):
                usage = {
                    "prompt_tokens": getattr(resp.usage_metadata, "prompt_token_count", 0),
                    "completion_tokens": getattr(resp.usage_metadata, "candidates_token_count", 0),
                }
            _track_cost(provider, usage["prompt_tokens"], usage["completion_tokens"])

            class StreamResult:
                def __init__(self):
                    self.content = "".join(content_parts) or None
                    self.tool_calls = tool_calls
                    self.usage = type("U", (), usage)()

            yield None, StreamResult(), usage

        else:
            # Generic fallback: use non-streaming call
            result = ProviderRouter.call(messages, tools_schemas, provider)
            yield None, result, {}


def _available_providers() -> list:
    """Providers whose key is set (or that need none). Presence only — see
    _live_providers for validated liveness."""
    out = []
    for name, cfg in PROVIDER_CONFIGS.items():
        env_key = cfg.get("env", "")
        if not env_key or os.getenv(env_key):
            out.append(name)
    return out


_LIVE_CACHE: dict[str, tuple[float, bool]] = {}
_LIVE_TTL = float(os.getenv("HERMES_PROVIDER_PROBE_TTL", "300"))


def _probe_provider(name: str) -> bool:
    """Actually verify a provider answers (key VALIDITY, not just presence).
    Uses free endpoints only (GET /models). Local providers probed without a key."""
    import requests as _rq

    cfg = PROVIDER_CONFIGS.get(name)
    if not cfg:
        return False
    env_key = cfg.get("env", "")
    key = os.getenv(env_key, "") if env_key else ""
    if cfg.get("local"):
        if env_key and not key:
            return False  # gateway (e.g. freellmapi) requires a key that isn't configured
        try:
            base = cfg.get("base", "").rstrip("/")
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            return _rq.get(f"{base}/models", headers=headers, timeout=2).status_code < 500
        except Exception:
            return False
    if env_key and not key:
        return False
    if cfg.get("lib") == "anthropic":
        try:
            r = _rq.get("https://api.anthropic.com/v1/models", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=4)
            return r.status_code == 200
        except Exception:
            return False
    if cfg.get("lib") == "openai" and cfg.get("base"):
        try:
            r = _rq.get(f"{cfg['base'].rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}, timeout=4)
            return r.status_code == 200
        except Exception:
            return False
    if cfg.get("lib") == "openai" and not cfg.get("base"):  # api.openai.com
        try:
            r = _rq.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=4)
            return r.status_code == 200
        except Exception:
            return False
    return bool(key)  # google/cohere etc: fall back to key presence


def _provider_alive(name: str) -> bool:
    now = time.time()
    cached = _LIVE_CACHE.get(name)
    if cached and now - cached[0] < _LIVE_TTL:
        return cached[1]
    alive = _probe_provider(name)
    _LIVE_CACHE[name] = (now, alive)
    return alive


def _live_providers() -> list:
    """Validated-live providers: local endpoints that answer + cloud keys that work.
    Probes run concurrently and are cached for HERMES_PROVIDER_PROBE_TTL seconds."""
    candidates = [n for n, cfg in PROVIDER_CONFIGS.items() if cfg.get("local") or not cfg.get("env") or os.getenv(cfg.get("env", ""))]
    now = time.time()
    stale = [n for n in candidates if n not in _LIVE_CACHE or now - _LIVE_CACHE[n][0] >= _LIVE_TTL]
    if stale:
        with ThreadPoolExecutor(max_workers=min(8, len(stale))) as pool:
            for name, alive in zip(stale, pool.map(_probe_provider, stale)):
                _LIVE_CACHE[name] = (now, alive)
    return [n for n in candidates if _LIVE_CACHE.get(n, (0, False))[1]]
