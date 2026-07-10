"""Tests for core.scaffold — text-to-app project generation (offline, deterministic)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scaffold import kinds, scaffold, _slug


def test_kinds_stable():
    ks = kinds()
    for expected in ("web", "api", "saas", "fullstack", "cli", "mobile", "ios", "android"):
        assert expected in ks


def test_slug():
    assert _slug("My Cool App!") == "my-cool-app"
    assert _slug("") == "app"
    assert _slug("___") == "app"


def test_web_scaffold_is_runnable(tmp_path):
    r = scaffold("web", "Demo", str(tmp_path / "demo"))
    assert r["ok"] and r["kind"] == "web"
    pkg = json.loads((tmp_path / "demo" / "package.json").read_text())
    assert pkg["scripts"]["dev"] == "vite" and "react" in pkg["dependencies"]
    assert (tmp_path / "demo" / "src" / "App.jsx").exists()
    assert "npm run dev" in r["run"]


def test_api_scaffold(tmp_path):
    r = scaffold("api", "svc", str(tmp_path / "svc"))
    main = (tmp_path / "svc" / "main.py").read_text()
    assert "FastAPI" in main and 'title="svc"' in main
    assert (tmp_path / "svc" / "Dockerfile").exists()
    assert "uvicorn" in r["run"]


def test_saas_is_frontend_plus_backend(tmp_path):
    r = scaffold("saas", "Widget", str(tmp_path / "w"))
    assert (tmp_path / "w" / "frontend" / "package.json").exists()
    assert (tmp_path / "w" / "backend" / "main.py").exists()
    assert (tmp_path / "w" / "docker-compose.yml").exists()
    assert "docker compose up" in r["run"]


def test_mobile_expo(tmp_path):
    r = scaffold("ios", "MyApp", str(tmp_path / "m"))
    pkg = json.loads((tmp_path / "m" / "package.json").read_text())
    assert "expo" in pkg["dependencies"]
    assert (tmp_path / "m" / "App.js").exists()
    assert "expo start" in r["run"]


def test_cli_scaffold(tmp_path):
    r = scaffold("cli", "mytool", str(tmp_path / "c"))
    py = json.loads
    toml = (tmp_path / "c" / "pyproject.toml").read_text()
    assert "[project.scripts]" in toml and "mytool" in toml
    assert (tmp_path / "c" / "mytool.py").exists()


def test_unknown_kind():
    r = scaffold("cobol", "x", "")
    assert not r["ok"] and "Unknown kind" in r["error"]


def test_default_dir_from_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = scaffold("web", "Auto Named")
    assert r["dir"] == "auto-named"
    assert (tmp_path / "auto-named" / "index.html").exists()
