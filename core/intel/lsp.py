"""Minimal LSP client — stdio transport, Content-Length framed JSON-RPC 2.0.

Real go-to-definition / references / diagnostics from pyright and
typescript-language-server, with graceful degradation when not installed.
Standalone: stdlib only. Never raises into the agent loop (callers get strings/lists).
"""

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

SERVERS = {
    ".py": ("pyright-langserver", ["pyright-langserver", "--stdio"], "npm install -g pyright"),
    ".ts": ("typescript-language-server", ["typescript-language-server", "--stdio"], "npm install -g typescript-language-server typescript"),
    ".tsx": ("typescript-language-server", ["typescript-language-server", "--stdio"], "npm install -g typescript-language-server typescript"),
    ".js": ("typescript-language-server", ["typescript-language-server", "--stdio"], "npm install -g typescript-language-server typescript"),
    ".jsx": ("typescript-language-server", ["typescript-language-server", "--stdio"], "npm install -g typescript-language-server typescript"),
}
LANGUAGE_IDS = {".py": "python", ".ts": "typescript", ".tsx": "typescriptreact", ".js": "javascript", ".jsx": "javascriptreact"}
DEFAULT_TIMEOUT = 15.0


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


class _Connection:
    def __init__(self, cmd: list[str], root: Path):
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.responses: queue.Queue[dict] = queue.Queue()
        self.diagnostics: dict[str, list] = {}
        self.diag_event = threading.Event()
        self.next_id = 1
        self.lock = threading.Lock()
        self.root = root
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    # ── framing ───────────────────────────────────────────────
    def _send(self, msg: dict):
        body = json.dumps(msg).encode()
        self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.proc.stdin.flush()

    def _read_loop(self):
        try:
            out = self.proc.stdout
            while True:
                headers = {}
                line = out.readline()
                if not line:
                    return
                while line and line.strip():
                    if b":" in line:
                        k, v = line.split(b":", 1)
                        headers[k.strip().lower()] = v.strip()
                    line = out.readline()
                length = int(headers.get(b"content-length", 0))
                if not length:
                    continue
                msg = json.loads(out.read(length))
                self._dispatch(msg)
        except (ValueError, OSError):
            pass

    def _dispatch(self, msg: dict):
        method = msg.get("method")
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            self.diagnostics[params.get("uri", "")] = params.get("diagnostics", [])
            self.diag_event.set()
        elif method and "id" in msg:
            # server -> client request (workspace/configuration, registerCapability, ...):
            # answer with a null-ish result so the server never blocks on us
            result: Any = None
            if method == "workspace/configuration":
                result = [None] * len(msg.get("params", {}).get("items", []))
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        elif "id" in msg:
            self.responses.put(msg)
        # plain notifications (logMessage etc.) are dropped

    # ── rpc ───────────────────────────────────────────────────
    def request(self, method: str, params: dict, timeout: float = DEFAULT_TIMEOUT):
        with self.lock:
            mid = self.next_id
            self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"LSP {method} timed out")
            try:
                msg = self.responses.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"LSP {method} timed out")
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "LSP error"))
                return msg.get("result")
            self.responses.put(msg)

    def notify(self, method: str, params: dict):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            try:
                self.proc.kill()
            except OSError:
                pass


class LspClient:
    def __init__(self, root: str = ".", servers: dict | None = None):
        self.root = Path(root).resolve()
        self.servers = servers or SERVERS
        self._conns: dict[str, _Connection] = {}
        self._opened: set = set()

    # ── connection management ─────────────────────────────────
    def _server_for(self, path: str):
        ext = Path(path).suffix.lower()
        return ext, self.servers.get(ext)

    def _connect(self, path: str) -> _Connection | None:
        ext, spec = self._server_for(path)
        if not spec:
            return None
        name, cmd, _hint = spec
        conn = self._conns.get(name)
        if conn and conn.proc.poll() is None:
            return conn
        if not shutil.which(cmd[0]):
            return None
        conn = _Connection(cmd, self.root)
        conn.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": _uri(self.root),
                "workspaceFolders": [{"uri": _uri(self.root), "name": self.root.name}],
                "capabilities": {"textDocument": {"publishDiagnostics": {}}},
            },
            timeout=20,
        )
        conn.notify("initialized", {})
        self._conns[name] = conn
        return conn

    def _open(self, conn: _Connection, path: str):
        p = Path(path)
        uri = _uri(p)
        if uri in self._opened:
            return uri
        conn.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": LANGUAGE_IDS.get(p.suffix.lower(), "plaintext"),
                    "version": 1,
                    "text": p.read_text(errors="replace"),
                }
            },
        )
        self._opened.add(uri)
        return uri

    def _unavailable(self, path: str) -> str:
        ext, spec = self._server_for(path)
        if not spec:
            return f"No language server registered for '{ext}' files"
        return f"{spec[0]} not installed — {spec[2]}"

    @staticmethod
    def _locations(result) -> list[dict]:
        if result is None:
            return []
        items = result if isinstance(result, list) else [result]
        out = []
        for it in items:
            uri = it.get("uri") or it.get("targetUri", "")
            rng = it.get("range") or it.get("targetSelectionRange") or {}
            start = rng.get("start", {})
            out.append(
                {
                    "file": uri.replace("file://", ""),
                    "line": start.get("line", 0) + 1,
                    "character": start.get("character", 0) + 1,
                }
            )
        return out

    # ── public API (never raises) ─────────────────────────────
    def available(self) -> dict[str, bool]:
        seen = {}
        for _ext, (name, cmd, _hint) in self.servers.items():
            seen[name] = bool(shutil.which(cmd[0]))
        return seen

    def definition(self, path: str, line: int, character: int):
        try:
            conn = self._connect(path)
            if not conn:
                return self._unavailable(path)
            uri = self._open(conn, path)
            result = conn.request(
                "textDocument/definition",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": max(0, line - 1), "character": max(0, character - 1)},
                },
            )
            return self._locations(result)
        except Exception as exc:
            return f"LSP definition failed: {exc}"

    def references(self, path: str, line: int, character: int):
        try:
            conn = self._connect(path)
            if not conn:
                return self._unavailable(path)
            uri = self._open(conn, path)
            result = conn.request(
                "textDocument/references",
                {
                    "textDocument": {"uri": uri},
                    "position": {"line": max(0, line - 1), "character": max(0, character - 1)},
                    "context": {"includeDeclaration": True},
                },
            )
            return self._locations(result)
        except Exception as exc:
            return f"LSP references failed: {exc}"

    def diagnostics(self, path: str, timeout: float = 10.0):
        try:
            conn = self._connect(path)
            if not conn:
                return self._unavailable(path)
            conn.diag_event.clear()
            uri = _uri(Path(path))
            self._opened.discard(uri)  # force re-open so the server re-publishes
            self._open(conn, path)
            deadline = time.monotonic() + timeout
            while uri not in conn.diagnostics and time.monotonic() < deadline:
                conn.diag_event.wait(0.25)
                conn.diag_event.clear()
            diags = conn.diagnostics.get(uri, [])
            return [
                {"line": d.get("range", {}).get("start", {}).get("line", 0) + 1, "severity": d.get("severity", 0), "message": d.get("message", "")[:300]}
                for d in diags
            ]
        except Exception as exc:
            return f"LSP diagnostics failed: {exc}"

    def close_all(self):
        for conn in self._conns.values():
            conn.close()
        self._conns = {}
        self._opened = set()
