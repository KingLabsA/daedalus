"""Tests for core.platform — MCP client, doctor, profiler, model advisor. All offline."""

import re
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.platform import PERSONAS, DependencyScanner, McpClient, ModelAdvisor, ProfileBuilder


def test_platform_no_agent_ultimate_dependency():
    import core.platform as plat

    for mod_file in Path(plat.__file__).parent.glob("*.py"):
        assert not re.search(r"^\s*(?:from|import)\s+agent_ultimate", mod_file.read_text(), re.M), mod_file.name


# ── MCP client (against a scripted fake stdio server) ────────

FAKE_SERVER = textwrap.dedent("""
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        method = msg.get("method", "")
        if "id" not in msg:
            continue  # notification
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake-server", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "description": "echoes input", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            args = msg["params"].get("arguments", {})
            result = {"content": [{"type": "text", "text": "ECHO:" + json.dumps(args)}]}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
""")


@pytest.fixture
def mcp(tmp_path):
    server_script = tmp_path / "fake_server.py"
    server_script.write_text(FAKE_SERVER)
    client = McpClient(str(tmp_path / "mcp.json"))
    client.add_server("fake", sys.executable, [str(server_script)])
    yield client
    client.close_all()


def test_mcp_connect_and_handshake(mcp):
    out = mcp.connect("fake")
    assert "Connected to 'fake'" in out and "fake-server" in out
    assert mcp.status() == {"fake": "connected"}


def test_mcp_list_and_call_tools(mcp):
    tools = mcp.list_tools("fake")  # auto-connects
    assert tools[0]["name"] == "echo"
    result = mcp.call_tool("fake", "echo", {"msg": "hi"})
    assert result == 'ECHO:{"msg": "hi"}'


def test_mcp_unknown_server(tmp_path):
    client = McpClient(str(tmp_path / "mcp.json"))
    assert "Unknown MCP server" in client.connect("ghost")
    with pytest.raises(RuntimeError):
        client.list_tools("ghost")


def test_mcp_bad_command(tmp_path):
    client = McpClient(str(tmp_path / "mcp.json"))
    client.add_server("broken", "/nonexistent/binary")
    assert "Failed to spawn" in client.connect("broken")


def test_mcp_config_roundtrip(tmp_path):
    client = McpClient(str(tmp_path / "mcp.json"))
    client.add_server("a", "npx", ["-y", "some-server"], {"KEY": "v"})
    servers = client.servers()
    assert servers["a"]["args"] == ["-y", "some-server"]
    assert servers["a"]["env"] == {"KEY": "v"}


# ── DependencyScanner ────────────────────────────────────────


def test_doctor_missing_and_ok():
    scanner = DependencyScanner(
        which_fn=lambda name: "/usr/bin/" + name if name in ("git", "node") else None,
        provider_configs={"openai": {"env": "OPENAI_API_KEY"}, "ollama": {"env": ""}},
        env={"OPENAI_API_KEY": "sk-x"},
    )
    report = scanner.scan()
    assert "git" in report["ok"]
    missing_names = {m["name"] for m in report["missing"]}
    assert "ffmpeg" in missing_names and "sox" in missing_names
    assert report["providers"]["openai"] == "ready"
    assert report["providers"]["ollama"] == "ready"  # no key needed
    assert set(report["providers_live"]) == {"openai", "ollama"}


def test_doctor_provider_missing_key():
    scanner = DependencyScanner(
        which_fn=lambda name: None,
        provider_configs={"groq": {"env": "GROQ_API_KEY"}},
        env={},
    )
    report = scanner.scan()
    assert report["providers"]["groq"] == "missing GROQ_API_KEY"


def test_doctor_fix_script_and_summary():
    scanner = DependencyScanner(which_fn=lambda name: None, provider_configs={}, env={})
    report = scanner.scan()
    script = scanner.fix_script(report)
    assert "brew install ffmpeg" in script and "video" in script
    summary = scanner.summary(report)
    assert "missing" in summary.lower()
    all_ok = DependencyScanner(which_fn=lambda name: "/bin/x", provider_configs={}, env={})
    # python packages may still be missing in the test env; only check binaries went ok
    assert "MISSING ffmpeg" not in all_ok.summary()


# ── ProfileBuilder ───────────────────────────────────────────


class FakeStore:
    def __init__(self):
        self.memories = []

    def add_memory(self, content, kind="note", importance=0.5):
        self.memories.append({"content": content, "kind": kind, "importance": importance})
        return len(self.memories)


def _builder(tmp_path):
    saved_skills = []
    store = FakeStore()
    builder = ProfileBuilder(
        profile_path=str(tmp_path / "profile.json"),
        save_skill_fn=lambda n, d, w: saved_skills.append(n),
        memory_store=store,
    )
    return builder, saved_skills, store


def test_persona_matching_aliases():
    assert ProfileBuilder.match_persona("I'm a backend dev") == "developer"
    assert ProfileBuilder.match_persona("Product Manager") == "project_manager"
    assert ProfileBuilder.match_persona("physician at a hospital") == "doctor_medical"
    assert ProfileBuilder.match_persona("mechanical engineer") == "engineer"
    assert ProfileBuilder.match_persona("phd researcher") == "researcher"
    assert ProfileBuilder.match_persona("total mystery") == "developer"  # default


def test_build_developer_profile(tmp_path):
    builder, saved_skills, store = _builder(tmp_path)
    profile = builder.build({"role": "dev", "domains": "web apps", "stack": "python", "experience": "expert", "goals": "ship faster"})
    assert profile["persona"] == "developer"
    assert "pack_tdd_loop" in saved_skills
    assert builder.exists()
    assert any("ship faster" in m["content"] for m in store.memories)
    addendum = builder.system_addendum()
    assert "software developer" in addendum.lower() and "ship faster" in addendum


def test_build_doctor_profile_differs(tmp_path):
    builder, saved_skills, store = _builder(tmp_path)
    builder.build({"role": "doctor", "domains": "cardiology", "stack": "", "experience": "beginner", "goals": "summarize studies"})
    assert "pack_literature_summary" in saved_skills
    assert "pack_tdd_loop" not in saved_skills
    assert "medical" in builder.system_addendum().lower()


def test_interview_uses_ask_fn(tmp_path):
    builder, _, _ = _builder(tmp_path)
    answers = builder.interview(lambda q: "yes")
    assert set(answers) == {"role", "domains", "stack", "experience", "goals"}
    assert all(v == "yes" for v in answers.values())


def test_all_personas_have_required_fields():
    for key, persona in PERSONAS.items():
        assert persona["label"] and persona["addendum"], key
        for name, (desc, workflow) in persona["skills"].items():
            assert name.startswith("pack_") and desc and workflow, key


# ── ModelAdvisor ─────────────────────────────────────────────


def _advisor(ram_gb, apple=True, installed=None, providers=None, env=None):
    return ModelAdvisor(
        spec_probe=lambda: {
            "os": "darwin",
            "arch": "arm64" if apple else "x86_64",
            "cpu_cores": 8,
            "ram_gb": ram_gb,
            "apple_silicon": apple,
            "nvidia_gpu": False,
        },
        ollama_list=lambda: installed or [],
        provider_configs=providers or {},
        env=env or {},
    )


def test_advisor_tiers_by_ram():
    small = _advisor(8).advise()
    big = _advisor(24).advise()
    small_models = {m["model"] for m in small["local_models"]}
    big_models = {m["model"] for m in big["local_models"]}
    assert any("qwen2.5-coder:7b" in m for m in small_models)
    assert not any("32b" in m for m in small_models)
    assert any("32b" in m for m in big_models)


def test_advisor_non_apple_discounts_ram():
    apple = _advisor(16, apple=True).advise()
    intel = _advisor(16, apple=False).advise()
    assert apple["usable_memory_gb"] > intel["usable_memory_gb"]


def test_advisor_flags_installed_and_cloud():
    advice = _advisor(
        24,
        installed=["hermes3:8b", "king3djbl/mythos-v2-8b-q4:latest"],
        providers={"fable": {"env": "ANTHROPIC_API_KEY"}, "groq": {"env": "GROQ_API_KEY"}},
        env={"ANTHROPIC_API_KEY": "sk"},
    ).advise()
    installed_flags = {m["model"]: m["installed"] for m in advice["local_models"]}
    assert installed_flags.get("hermes3:8b") is True
    assert "fable" in advice["cloud_providers"] and "groq" not in advice["cloud_providers"]
    assert "claude-fable-5" in advice["recommended"]["cloud"]


def test_advisor_render_readable():
    text = _advisor(24, installed=["hermes3:8b"]).render()
    assert "Apple Silicon" in text and "RECOMMENDED" in text and "[installed]" in text
