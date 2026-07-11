#!/usr/bin/env python3
"""daedalus — launcher for Daedalus (powered by the Hermes Deep Mind engine).

  daedalus          rich terminal UI (falls back to plain CLI without `rich`)
  daedalus web      web IDE: serves the built frontend + agent WS server, one process
  daedalus ws       headless agent WebSocket server
  daedalus doctor   scan this device for missing dependencies
  daedalus models   what models can this machine run
  daedalus version  print version
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "2.0.4"


# ── helpers (unit-tested) ─────────────────────────────────────

def inject_token(html: str, token: str) -> str:
    """Idempotently inject the WS auth token into served index.html."""
    marker = "window.HERMES_TOKEN"
    if marker in html:
        return html
    script = f"<script>window.HERMES_TOKEN={json.dumps(token)}</script>"
    if "<head>" in html:
        return html.replace("<head>", "<head>" + script, 1)
    return script + html


def find_dist(root: Path = ROOT) -> Path:
    """Repo build first (dev), then the pip-bundled hermes_webui package."""
    repo_dist = root / "desktop" / "dist"
    if (repo_dist / "index.html").is_file() or root != ROOT:
        return repo_dist
    try:
        import hermes_webui
        bundled = hermes_webui.dist_path()
        if (bundled / "index.html").is_file():
            return bundled
    except ImportError:
        pass
    return repo_dist


def dist_ready(root: Path = ROOT) -> bool:
    return (find_dist(root) / "index.html").is_file()


def build_frontend(root: Path = ROOT) -> bool:
    if not shutil.which("npm"):
        return False
    desktop = root / "desktop"
    try:
        if not (desktop / "node_modules").exists():
            subprocess.run(["npm", "install"], cwd=desktop, check=True, timeout=600)
        subprocess.run(["npm", "run", "build"], cwd=desktop, check=True, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return dist_ready(root)


# ── web IDE ───────────────────────────────────────────────────

class _SpaHandler(SimpleHTTPRequestHandler):
    """Static file server with SPA fallback and token-injected index.html."""
    token = ""

    def _serve_index(self):
        index = Path(self.directory) / "index.html"
        body = inject_token(index.read_text(), self.token).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._serve_index()
        candidate = Path(self.directory) / path.lstrip("/")
        if candidate.is_file():
            return super().do_GET()
        return self._serve_index()  # SPA fallback

    def log_message(self, fmt, *args):
        pass  # keep the console clean


def cmd_web(port: int, no_browser: bool = False):
    if not dist_ready():
        print("Frontend not built — building now (one-time, needs npm)...")
        if not build_frontend():
            sys.exit("Could not build the frontend. Install node/npm (brew install node), "
                     "then run: cd desktop && npm install && npm run build")
    token = secrets.token_hex(16)
    os.environ["HERMES_WS_TOKEN"] = token

    sys.path.insert(0, str(ROOT))
    import asyncio
    from agent_ultimate import UltimateAgent, run_ws_server
    agent = UltimateAgent()
    ws_thread = threading.Thread(target=lambda: asyncio.run(run_ws_server(agent)), daemon=True, name="hermes-ws")
    ws_thread.start()

    handler = partial(_SpaHandler, directory=str(find_dist()))
    _SpaHandler.token = token
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Daedalus Web IDE  ->  {url}   (agent ws://127.0.0.1:8765, token-protected)")
    print("Ctrl-C to stop.")
    if not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        agent.subconscious.stop()


# ── rich TUI ──────────────────────────────────────────────────

def cmd_tui():
    sys.path.insert(0, str(ROOT))
    from agent_ultimate import UltimateAgent, PROVIDER_CONFIGS, MODEL_NAME
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.panel import Panel
    except ImportError:
        print("(tip: pip install rich for the full TUI)")
        UltimateAgent().chat()
        return

    console = Console()
    agent = UltimateAgent()

    # persistent input history (up-arrow) across sessions
    try:
        import readline
        hist = Path.home() / ".hermes" / "cli_history"
        hist.parent.mkdir(parents=True, exist_ok=True)
        if hist.exists():
            readline.read_history_file(str(hist))
        readline.set_history_length(1000)
        import atexit
        atexit.register(lambda: readline.write_history_file(str(hist)))
    except Exception:
        pass

    # diff-approve: render destructive tool calls and ask before they run
    def _approve(tool: str, args: dict, preview: str) -> bool:
        from rich.syntax import Syntax
        lang = "diff" if preview.startswith(("---", "@@", "-", "+")) or "\n+" in preview else "bash"
        console.print(Panel(Syntax(preview, lang, theme="ansi_dark", word_wrap=True),
                            title=f"[yellow]approve {tool}?[/]", border_style="yellow"))
        try:
            ans = console.input("[yellow]apply? [y/N/a=allow-all] ▸ [/]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if ans == "a":
            agent.safety.mode = "auto"
            console.print("[dim]safety → auto (approving remaining writes this session)[/]")
            return True
        return ans in ("y", "yes")
    agent.approve_fn = _approve

    if not agent.profiler.exists() and sys.stdin.isatty():
        agent.first_launch_setup()

    stats = agent.context.stats()
    profile = agent.profiler.load() or {}
    console.print(Panel.fit(
        f"[bold magenta]DAEDALUS[/] [dim]v{VERSION} — Hermes Deep Mind engine[/]\n"
        f"provider [cyan]{agent.provider}[/] ({MODEL_NAME}) · auto-routing [green]on[/]\n"
        f"[dim]{stats['memories']} memories · {stats['failures']} antibodies · "
        f"{len(agent.registry.list_tools())} tools · persona: {profile.get('persona_label', '—')}[/]\n"
        f"[dim]/help commands · /reset new chat · ↑ history · Ctrl-C interrupt · exit to quit[/]",
        border_style="magenta",
    ))

    while True:
        try:
            u = console.input("[bold cyan]you ▸ [/]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not u:
            continue
        if u in ("exit", "quit"):
            break
        if u == "/help":
            console.print(Panel(agent.COMMANDS_HELP, title="commands", border_style="dim"))
            continue
        try:
            if u.startswith("/"):
                with console.status("[dim]working…[/]"):
                    handled = agent.handle_command(u, ask_fn=lambda q: console.input(f"[yellow]{q}[/]\n> "))
                if handled is not None:
                    if handled.strip().startswith(("{", "[")):
                        console.print_json(handled)
                    elif handled:
                        console.print(handled)
                    continue
            streamed = {"n": 0}
            def _on_token(t: str):
                streamed["n"] += 1
                console.print(t, end="", style="dim", highlight=False, soft_wrap=True)
            agent.on_token = _on_token
            interrupted = False
            try:
                result = agent.converse(u)  # multi-turn continuity
            except KeyboardInterrupt:
                interrupted = True
                agent.cancel_event.set()  # stop any in-flight stream cleanly
                result = "[interrupted by user]"
            finally:
                agent.on_token = None
            if streamed["n"]:
                console.print()  # end the raw token stream before the rendered panel
            if interrupted:
                console.print("[yellow]⏹ interrupted[/]")
                continue
            routed = next((l for l in reversed(agent.logs) if l.get("type") == "auto_route"), None)
            failover = [l for l in agent.logs if l.get("type") == "provider_failover"]
            subtitle = f"routed → {routed['provider']} (tier {routed['tier']})" if routed else agent.provider
            if failover:
                subtitle += f" · failover→{failover[-1]['to']}"
            console.print(Panel(Markdown(result or ""), title="hermes", subtitle=f"[dim]{subtitle}[/]",
                                border_style="blue", title_align="left", subtitle_align="right"))
        except KeyboardInterrupt:
            console.print("\n[yellow]⏹ interrupted[/]")
        except Exception as exc:
            console.print(f"[red]error:[/] {exc}")
    agent.subconscious.stop()
    console.print("[dim]bye.[/]")


# ── one-shot utilities ────────────────────────────────────────

def cmd_ws():
    sys.path.insert(0, str(ROOT))
    import asyncio
    from agent_ultimate import UltimateAgent, run_ws_server, WS_HOST, WS_PORT
    print(f"Daedalus agent server (Hermes engine) on ws://{WS_HOST}:{WS_PORT}")
    asyncio.run(run_ws_server(UltimateAgent()))


def cmd_run(task: str, provider: str = "", auto: bool = False, as_json: bool = False) -> int:
    """One-shot headless: run a single task, print the result, exit. For CI,
    scripts, and git hooks. Exit 0 on success, 1 on error/interrupt."""
    import json as _json
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("HERMES_SUBCONSCIOUS", "off")
    from agent_ultimate import UltimateAgent
    agent = UltimateAgent()
    if provider:
        agent.provider = provider
        agent._provider_pinned = True
    if auto:
        agent.safety.mode = "auto"
    try:
        result = agent.converse(task)
    except Exception as exc:
        print(_json.dumps({"ok": False, "error": str(exc)}) if as_json else f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            agent.subconscious.stop()
        except Exception:
            pass
    routed = next((l for l in reversed(agent.logs) if l.get("type") == "auto_route"), None)
    routed_to = routed["provider"] if routed else agent.provider
    if as_json:
        cs = agent.changesets.summary()
        print(_json.dumps({"ok": True, "result": result, "routed_to": routed_to,
                           "files_changed": [f["path"] for f in cs["files"]]}))
    else:
        print(result)
    return 0


def cmd_doctor():
    sys.path.insert(0, str(ROOT))
    from agent_ultimate import PROVIDER_CONFIGS, _live_providers
    from core.platform import DependencyScanner
    scanner = DependencyScanner(provider_configs=PROVIDER_CONFIGS)
    report = scanner.scan()
    print(scanner.summary(report))
    print()
    print("Probing providers (validated, not just key-present)...")
    live = _live_providers()
    print("LIVE NOW:", ", ".join(live) if live else "none")
    if "freellmapi" not in live:
        print("  hint: FreeLLMAPI is configured but not running — launch it (localhost:3002) to unlock 67 models")
    if "ollama" not in live:
        print("  hint: start Ollama for free local models (ollama serve)")
    print()
    print(scanner.fix_script(report))


def cmd_models():
    sys.path.insert(0, str(ROOT))
    from agent_ultimate import PROVIDER_CONFIGS
    from core.platform import ModelAdvisor
    print(ModelAdvisor(provider_configs=PROVIDER_CONFIGS).render())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="daedalus", description="Daedalus — coding assistant powered by the Hermes Deep Mind engine")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("tui", help="rich terminal UI (default)")
    web = sub.add_parser("web", help="web IDE (serves frontend + agent)")
    web.add_argument("--port", type=int, default=8899)
    web.add_argument("--no-browser", action="store_true")
    app = sub.add_parser("app", help="standalone native desktop window (pywebview)")
    app.add_argument("--port", type=int, default=8900)
    run_p = sub.add_parser("run", help="one-shot headless: run a task, print result, exit (CI/scripts)")
    run_p.add_argument("task", nargs="+", help="the task to run")
    run_p.add_argument("--provider", default="", help="pin a provider (else auto-route)")
    run_p.add_argument("--yes", action="store_true", help="auto-approve file writes/commands")
    run_p.add_argument("--json", action="store_true", help="emit JSON (result, routed_to, files_changed)")
    sub.add_parser("ws", help="headless agent WebSocket server")
    sub.add_parser("doctor", help="scan device for missing dependencies")
    sub.add_parser("models", help="models this machine can run")
    sub.add_parser("version", help="print version")
    args = parser.parse_args(argv)

    if args.cmd == "web":
        cmd_web(args.port, args.no_browser)
    elif args.cmd == "app":
        sys.path.insert(0, str(ROOT))
        import desktop_app
        desktop_app.run(port=args.port)
    elif args.cmd == "run":
        sys.exit(cmd_run(" ".join(args.task), args.provider, args.yes, args.json))
    elif args.cmd == "ws":
        cmd_ws()
    elif args.cmd == "doctor":
        cmd_doctor()
    elif args.cmd == "models":
        cmd_models()
    elif args.cmd == "version":
        print(f"daedalus {VERSION} (hermes engine)")
    else:
        cmd_tui()


if __name__ == "__main__":
    main()
