"""Tests for core.scaffold — text-to-app project generation (offline, deterministic)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.scaffold import _slug, kinds, scaffold


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


def test_new_template_kinds_present():
    for k in ("tailwind", "shadcn", "supabase", "astro", "svelte", "mcp"):
        assert k in kinds()


def test_tailwind_has_config(tmp_path):
    scaffold("tailwind", "T", str(tmp_path))
    assert (tmp_path / "tailwind.config.js").exists()
    assert "@tailwind base" in (tmp_path / "src" / "index.css").read_text()


def test_supabase_client(tmp_path):
    scaffold("supabase", "S", str(tmp_path))
    assert "createClient" in (tmp_path / "src" / "supabaseClient.js").read_text()
    assert (tmp_path / ".env.example").exists()


def test_mcp_server_scaffold(tmp_path):
    r = scaffold("mcp", "Tools", str(tmp_path))
    assert r["ok"]
    assert "tools/call" in (tmp_path / "server.py").read_text()
    cfg = json.loads((tmp_path / ".hermes" / "mcp.json").read_text())
    assert "tools" in cfg["servers"]  # slug of "Tools"


def test_astro_and_svelte(tmp_path):
    scaffold("astro", "A", str(tmp_path / "a"))
    assert (tmp_path / "a" / "src" / "pages" / "index.astro").exists()
    scaffold("sveltekit", "V", str(tmp_path / "v"))
    assert (tmp_path / "v" / "src" / "routes" / "+page.svelte").exists()


def test_canonical_command_strings():
    from core.scaffold import canonical_command

    assert "create vite" in canonical_command("web", "My App")
    assert "my-app" in canonical_command("web", "My App")
    assert "create-next-app" in canonical_command("next", "x")
    assert "create-expo-app" in canonical_command("ios", "x")
    assert "t3-app" in canonical_command("t3", "x")
    assert canonical_command("mcp", "x") == ""  # no canonical CLI for our mcp kind


def test_official_prefers_cli_when_present():
    from core.scaffold import official

    r = official("web", "Demo", which=lambda c: "/usr/bin/" + c)
    assert r["mode"] == "official" and "create vite" in r["command"]


def test_official_falls_back_to_skeleton(tmp_path, monkeypatch):
    from core.scaffold import official

    monkeypatch.chdir(tmp_path)
    r = official("web", "Demo", which=lambda c: None)  # no npm/npx
    assert r["mode"] == "skeleton" and r["ok"]
    assert (tmp_path / "demo" / "package.json").exists()
    assert "create vite" in r["canonical_available_via"]


def test_official_unknown_kind_skeleton(tmp_path, monkeypatch):
    from core.scaffold import official

    monkeypatch.chdir(tmp_path)
    r = official("mcp", "M", which=lambda c: "/bin/" + c)  # mcp has no canonical
    assert r["mode"] == "skeleton"
