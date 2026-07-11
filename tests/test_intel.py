"""Tests for core.intel — code intelligence, semantic search, causal world model, sentinel."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intel import CausalWorldModel, CodeIntel, SemanticIndex, WorldModelSentinel
from core.intel.sentinel import WM_BEGIN


def test_intel_no_agent_ultimate_dependency():
    import core.intel as intel

    for mod_file in Path(intel.__file__).parent.glob("*.py"):
        assert not re.search(r"^\s*(?:from|import)\s+agent_ultimate", mod_file.read_text(), re.M), mod_file.name


# ── CodeIntel ────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path):
    (tmp_path / "auth.py").write_text(
        "class LoginManager:\n    def login(self, user):\n        return validate_token(user)\n\ndef validate_token(user):\n    return True\n"
    )
    (tmp_path / "app.js").write_text("export function renderDashboard() {}\nclass ApiClient {}\nconst fetchUser = async () => {}\n")
    (tmp_path / "main.py").write_text("from auth import LoginManager\n\nmanager = LoginManager()\n")
    return tmp_path


def test_python_symbols(project):
    syms = CodeIntel(str(project)).symbols(str(project / "auth.py"))
    names = {s["name"]: s for s in syms}
    assert names["LoginManager"]["kind"] == "class"
    assert "LoginManager.login" in names
    assert names["validate_token"]["kind"] == "function"


def test_js_symbols(project):
    syms = CodeIntel(str(project)).symbols(str(project / "app.js"))
    names = {s["name"] for s in syms}
    assert {"renderDashboard", "ApiClient", "fetchUser"} <= names


def test_find_definition_and_references(project):
    ci = CodeIntel(str(project))
    defs = ci.find_definition("LoginManager")
    assert defs and defs[0]["file"] == "auth.py" and defs[0]["kind"] == "class"
    refs = ci.references("LoginManager")
    assert {r["file"] for r in refs} == {"auth.py", "main.py"}


def test_diagnostics_syntax_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)  # force stdlib path
    (tmp_path / "good.py").write_text("x = 1\n")
    ok = CodeIntel(str(tmp_path)).diagnostics(".")
    assert "compile cleanly" in ok
    (tmp_path / "bad.py").write_text("def broken(:\n")
    issues = CodeIntel(str(tmp_path)).diagnostics(".")
    assert "bad.py" in issues


# ── SemanticIndex ────────────────────────────────────────────


def test_semantic_search_relevance(tmp_path):
    (tmp_path / "payments.py").write_text("def charge_credit_card(amount, card_number):\n    '''Process a payment charge'''\n    pass\n")
    (tmp_path / "animals.py").write_text("def feed_zebra():\n    pass\n")
    idx = SemanticIndex(str(tmp_path))
    hits = idx.search("process credit card payment")
    assert hits and hits[0]["file"] == "payments.py"
    assert hits[0]["score"] > 0


def test_semantic_search_tokenizes_camel_and_snake(tmp_path):
    (tmp_path / "svc.ts").write_text("export function getUserProfile() {}\n")
    hits = SemanticIndex(str(tmp_path)).search("user profile")
    assert hits and hits[0]["file"] == "svc.ts"


def test_semantic_search_empty_query(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert SemanticIndex(str(tmp_path)).search("!!! ???") == []


# ── CausalWorldModel ─────────────────────────────────────────


@pytest.fixture
def git_repo(tmp_path):
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    run("config", "user.email", "t@t.t")
    run("config", "user.name", "t")
    core = tmp_path / "core.py"
    api = tmp_path / "api.py"
    docs = tmp_path / "README.md"
    # core.py and api.py change together 3 times; README changes alone
    for i in range(3):
        core.write_text(f"VERSION = {i}\ndef core_fn():\n    pass\n")
        api.write_text(f"from core import core_fn\nREV = {i}\n")
        run("add", "-A")
        run("commit", "-qm", f"change {i}")
    docs.write_text("# readme\n")
    run("add", "-A")
    run("commit", "-qm", "docs only")
    return tmp_path


def test_worldmodel_co_change_mining(git_repo):
    wm = CausalWorldModel(str(git_repo))
    wm.build()
    related = dict(wm.co_changed("core.py"))
    assert "api.py" in related and related["api.py"] == 1.0
    assert "README.md" not in related


def test_worldmodel_blast_radius_ordering(git_repo):
    wm = CausalWorldModel(str(git_repo))
    wm.build()
    risky = wm.blast_radius("core.py")
    safe = wm.blast_radius("README.md")
    assert risky["risk"] > safe["risk"]
    assert "api.py" in risky["importers"]  # import fan-in mined via ast


def test_worldmodel_warning_threshold(git_repo):
    wm = CausalWorldModel(str(git_repo))
    wm.build()
    assert wm.render_warning("README.md") == ""
    # a file both imported and co-changed should warn only if risk >= threshold
    warning = wm.render_warning("core.py")
    if warning:
        assert "BLAST RADIUS" in warning and "api.py" in warning


def test_worldmodel_outside_git_is_graceful(tmp_path):
    wm = CausalWorldModel(str(tmp_path))
    stats = wm.build()
    assert stats["co_change_pairs"] == 0
    assert wm.blast_radius("anything.py")["risk"] == 0.0


# ── WorldModelSentinel ───────────────────────────────────────


class FakeWM:
    def __init__(self, warning="[WORLD MODEL] danger in hot.py"):
        self.warning = warning

    def render_warning(self, filepath):
        return self.warning if filepath == "hot.py" else ""


def test_sentinel_injects_and_clears():
    sentinel = WorldModelSentinel(FakeWM())
    sentinel._on_pre_tool(
        calls=[
            {"name": "write_file", "args": {"filepath": "hot.py"}},
            {"name": "write_file", "args": {"filepath": "cold.py"}},
            {"name": "read_file", "args": {"filepath": "hot.py"}},  # not a write tool
        ]
    )
    messages = [{"role": "system", "content": "base"}, {"role": "user", "content": "go"}]
    sentinel._on_pre_llm(messages=messages)
    assert WM_BEGIN in messages[0]["content"] and "danger in hot.py" in messages[0]["content"]
    # warnings clear after injection; block removed on next pass
    sentinel._on_pre_llm(messages=messages)
    assert "danger in hot.py" not in messages[0]["content"]


def test_sentinel_never_raises():
    sentinel = WorldModelSentinel(FakeWM())
    sentinel._on_pre_tool(calls=None)
    sentinel._on_pre_llm(messages=None)
    sentinel._on_pre_llm(messages=[{"role": "user", "content": "no system msg"}])
