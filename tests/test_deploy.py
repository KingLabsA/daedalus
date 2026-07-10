"""Tests for core.deploy — project detection + deploy planning (offline)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.deploy import detect, plan, targets
from core.scaffold import scaffold


def _mk(tmp_path, kind, name="app"):
    scaffold(kind, name, str(tmp_path))
    return str(tmp_path)


def test_detect_static(tmp_path):
    _mk(tmp_path, "web")
    assert detect(str(tmp_path)) == "static"


def test_detect_python_and_fullstack(tmp_path):
    # the api template ships a Dockerfile -> detected as docker (still fly-deployable)
    api = tmp_path / "api"; scaffold("api", "svc", str(api))
    assert detect(str(api)) == "docker"
    assert "fly" in targets(detect(str(api)), "svc")
    fs = tmp_path / "fs"; scaffold("saas", "w", str(fs))
    assert detect(str(fs)) == "fullstack"


def test_detect_expo(tmp_path):
    _mk(tmp_path, "ios", "m")
    assert detect(str(tmp_path)) == "expo"


def test_detect_unknown(tmp_path):
    assert detect(str(tmp_path)) == "unknown"


def test_plan_lists_targets_when_blank(tmp_path):
    _mk(tmp_path, "web")
    r = plan(str(tmp_path))
    assert r["ok"] and "vercel" in r["targets"] and "netlify" in r["targets"]


def test_plan_writes_vercel_config(tmp_path):
    _mk(tmp_path, "web")
    r = plan(str(tmp_path), "vercel", which=lambda c: "/usr/bin/" + c)
    assert r["ok"] and r["cli"] == "vercel" and r["cli_installed"]
    assert "vercel.json" in r["config_written"]
    assert (tmp_path / "vercel.json").exists()
    assert any("vercel --prod" in s for s in r["steps"])


def test_plan_reports_missing_cli(tmp_path):
    _mk(tmp_path, "web")
    r = plan(str(tmp_path), "netlify", which=lambda c: None)
    assert r["ok"] and not r["cli_installed"]
    assert "netlify-cli" in r["install"]


def test_plan_python_fly_writes_dockerfile(tmp_path):
    scaffold("cli", "x", str(tmp_path))  # python project, no Dockerfile from cli template
    (tmp_path / "main.py").write_text("app = 1\n")  # make it look like an app
    r = plan(str(tmp_path), "fly", which=lambda c: "/bin/" + c)
    assert r["ok"] and (tmp_path / "fly.toml").exists()


def test_plan_expo_eas(tmp_path):
    _mk(tmp_path, "android", "m")
    r = plan(str(tmp_path), "eas", which=lambda c: "/bin/" + c)
    assert r["ok"] and r["cli"] == "eas"
    assert (tmp_path / "eas.json").exists()
    assert any("eas build" in s for s in r["steps"])


def test_plan_unknown_target(tmp_path):
    _mk(tmp_path, "web")
    r = plan(str(tmp_path), "heroku")
    assert not r["ok"] and "Unknown target" in r["error"]


def test_plan_missing_dir():
    assert not plan("/nope/nowhere")["ok"]
