"""Tests for core.evalgate — pre-deploy verification (offline, injectable runner)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.deploy import plan
from core.evalgate import checks_for, gate, run_checks
from core.scaffold import scaffold


def _pass_runner(cmd, cwd, timeout):
    return 0, "ok"


def _fail_build(cmd, cwd, timeout):
    return (1, "build error: X") if "build" in cmd else (0, "ok")


def test_checks_for_static_includes_build(tmp_path):
    scaffold("web", "w", str(tmp_path))
    names = [c["name"] for c in checks_for(str(tmp_path))]
    assert "npm install" in names and "npm run build" in names


def test_checks_for_python_compiles(tmp_path):
    scaffold("cli", "c", str(tmp_path))
    names = [c["name"] for c in checks_for(str(tmp_path))]
    assert "python compile" in names


def test_run_checks_all_pass(tmp_path):
    scaffold("web", "w", str(tmp_path))
    r = run_checks(str(tmp_path), runner=_pass_runner)
    assert r["ok"] and r["passed"]
    assert all(c["passed"] for c in r["checks"])


def test_run_checks_detects_failure(tmp_path):
    scaffold("web", "w", str(tmp_path))
    r = run_checks(str(tmp_path), runner=_fail_build)
    assert not r["passed"]
    assert any(c["name"] == "npm run build" and not c["passed"] for c in r["checks"])


def test_python_compile_catches_syntax_error(tmp_path):
    scaffold("cli", "c", str(tmp_path))
    (tmp_path / "broken.py").write_text("def x(:\n")
    r = run_checks(str(tmp_path), runner=_pass_runner)
    compile_check = next(c for c in r["checks"] if c["name"] == "python compile")
    assert not compile_check["passed"]


def test_mcp_handshake_real(tmp_path):
    scaffold("mcp", "M", str(tmp_path))
    r = run_checks(str(tmp_path))  # real handshake, no runner needed
    hs = next(c for c in r["checks"] if c["name"] == "mcp handshake")
    assert hs["passed"]


def test_gate_verdict(tmp_path):
    scaffold("web", "w", str(tmp_path))
    g = gate(str(tmp_path), runner=_pass_runner)
    assert g["passed"] and "PASS" in g["verdict"]
    g2 = gate(str(tmp_path), runner=_fail_build)
    assert not g2["passed"] and "BLOCKED" in g2["verdict"]


def test_deploy_blocked_when_verify_fails(tmp_path, monkeypatch):
    scaffold("web", "w", str(tmp_path))
    # make the gate fail by monkeypatching evalgate.gate
    import core.evalgate as eg

    monkeypatch.setattr(
        eg,
        "gate",
        lambda d, k="", runner=None: {
            "ok": True,
            "passed": False,
            "verdict": "BLOCKED — failing checks: npm run build",
            "checks": [{"name": "npm run build", "passed": False, "detail": "err"}],
        },
    )
    r = plan(str(tmp_path), "vercel", verify=True, which=lambda c: "/bin/" + c)
    assert not r["ok"] and r.get("blocked_by_eval")


def test_deploy_allowed_when_verify_passes(tmp_path, monkeypatch):
    scaffold("web", "w", str(tmp_path))
    import core.evalgate as eg

    monkeypatch.setattr(eg, "gate", lambda d, k="", runner=None: {"ok": True, "passed": True, "verdict": "PASS", "checks": []})
    r = plan(str(tmp_path), "vercel", verify=True, which=lambda c: "/bin/" + c)
    assert r["ok"] and r["target"] == "vercel"


def test_gate_missing_dir():
    assert not gate("/nope/x")["ok"]
