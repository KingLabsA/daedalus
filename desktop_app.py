#!/usr/bin/env python3
"""Daedalus standalone desktop — a REAL native app, not a Tauri/browser wrapper.

Runs everything in one process:
  - the agent WebSocket server (background thread)
  - the built web UI served over local HTTP (background thread)
  - a native OS window (pywebview → macOS WebKit / Windows WebView2 / GTK WebKit)
    pointed at the local UI, token-injected.

No Rust, no Tauri — sidesteps the tao/macOS-26 crash. Packaged with PyInstaller
into a double-clickable .app (see build_app.sh), it's a self-contained desktop
application that bundles Python, the agent, and the UI.

    python desktop_app.py         # dev
    daedalus app                  # via the CLI (falls back to `web` if pywebview absent)
"""

import os
import secrets
import sys
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _serve_ui(token: str, port: int):
    # reuse the launcher's SPA handler + dist resolution
    sys.path.insert(0, str(ROOT))
    import hermes_cli

    if not hermes_cli.dist_ready() and not hermes_cli.build_frontend():
        raise RuntimeError("Frontend not built. Run: cd desktop && npm install && npm run build")
    handler = partial(hermes_cli._SpaHandler, directory=str(hermes_cli.find_dist()))
    hermes_cli._SpaHandler.token = token
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.serve_forever()


def _start_agent():
    import asyncio

    from agent_ultimate import UltimateAgent, run_ws_server

    agent = UltimateAgent()
    asyncio.run(run_ws_server(agent))


def run(port: int = 8900, width: int = 1400, height: int = 900):
    try:
        import webview
    except ImportError:
        print("pywebview not installed — falling back to the browser IDE.\nFor the native window: pip install 'daedalus-ai[app]'")
        sys.path.insert(0, str(ROOT))
        import hermes_cli

        hermes_cli.cmd_web(port)
        return

    token = secrets.token_hex(16)
    os.environ["HERMES_WS_TOKEN"] = token

    threading.Thread(target=_start_agent, daemon=True, name="daedalus-agent").start()
    threading.Thread(target=_serve_ui, args=(token, port), daemon=True, name="daedalus-ui").start()

    webview.create_window(
        "Daedalus",
        f"http://127.0.0.1:{port}",
        width=width,
        height=height,
        min_size=(900, 600),
        background_color="#16162a",
    )
    webview.start()  # blocks until the window closes


if __name__ == "__main__":
    run()
