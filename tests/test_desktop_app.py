"""Tests for the standalone desktop launcher (desktop_app.py).

Doesn't open a real window (no display in CI) — verifies wiring, the pywebview
window params, and the graceful fallback to the browser IDE when pywebview is
absent.
"""
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_desktop_app_imports():
    import desktop_app
    assert hasattr(desktop_app, "run")
    assert callable(desktop_app.run)


def test_run_creates_native_window_with_fake_webview(monkeypatch):
    import desktop_app
    calls = {}
    fake = types.SimpleNamespace(
        create_window=lambda *a, **kw: calls.setdefault("window", (a, kw)),
        start=lambda: calls.setdefault("started", True),
    )
    monkeypatch.setitem(sys.modules, "webview", fake)
    # don't actually spin up servers
    monkeypatch.setattr(desktop_app.threading, "Thread",
                        lambda *a, **kw: types.SimpleNamespace(start=lambda: None))
    desktop_app.run(port=8912)
    assert calls.get("started") is True
    (args, kw) = calls["window"]
    assert args[0] == "Daedalus"
    assert "127.0.0.1:8912" in args[1]
    assert kw["min_size"] == (900, 600)


def test_run_falls_back_to_web_without_pywebview(monkeypatch):
    import desktop_app
    # simulate pywebview missing
    monkeypatch.setitem(sys.modules, "webview", None)
    import builtins
    real_import = builtins.__import__

    def no_webview(name, *a, **kw):
        if name == "webview":
            raise ImportError("no pywebview")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_webview)
    fell_back = {}
    fake_cli = types.SimpleNamespace(cmd_web=lambda port: fell_back.setdefault("port", port))
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_cli)
    desktop_app.run(port=8913)
    assert fell_back.get("port") == 8913


def test_spec_and_build_script_exist():
    assert (ROOT / "daedalus.spec").exists()
    assert (ROOT / "build_app.sh").exists()
    assert "Daedalus.app" in (ROOT / "daedalus.spec").read_text()
