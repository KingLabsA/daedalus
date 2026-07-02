"""Minimal real MCP (Model Context Protocol) client — stdio transport.

Speaks newline-delimited JSON-RPC 2.0 to servers spawned from .hermes/mcp.json:
    {"servers": {"<name>": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], "env": {}}}}
"""
import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 20.0


class _Connection:
    def __init__(self, name: str, proc: subprocess.Popen):
        self.name = name
        self.proc = proc
        self.responses: "queue.Queue[dict]" = queue.Queue()
        self.next_id = 1
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def _read_loop(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "id" in msg:  # responses only; notifications are dropped
                    self.responses.put(msg)
        except (ValueError, OSError):
            pass

    def rpc(self, method: str, params: Optional[dict] = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
        with self.lock:
            msg_id = self.next_id
            self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        deadline = timeout
        while True:
            try:
                msg = self.responses.get(timeout=deadline)
            except queue.Empty:
                raise TimeoutError(f"MCP server '{self.name}' did not answer {method} within {timeout}s")
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"MCP error from '{self.name}': {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})
            # response to a different id — keep it for whoever waits (rare, single-threaded use)
            self.responses.put(msg)

    def notify(self, method: str, params: Optional[dict] = None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            try:
                self.proc.kill()
            except OSError:
                pass


class McpClient:
    def __init__(self, config_path: str = ".hermes/mcp.json"):
        self.config_path = Path(config_path)
        self._connections: Dict[str, _Connection] = {}

    # ── Config ────────────────────────────────────────────────
    def servers(self) -> Dict[str, dict]:
        if not self.config_path.exists():
            return {}
        try:
            return json.loads(self.config_path.read_text()).get("servers", {})
        except ValueError:
            return {}

    def add_server(self, name: str, command: str, args: Optional[List[str]] = None, env: Optional[dict] = None):
        config = {"servers": self.servers()}
        config["servers"][name] = {"command": command, "args": args or [], "env": env or {}}
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, indent=2))

    # ── Connection ────────────────────────────────────────────
    def connect(self, name: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        if name in self._connections and self._connections[name].proc.poll() is None:
            return f"Already connected to '{name}'"
        spec = self.servers().get(name)
        if not spec:
            return f"Unknown MCP server '{name}'. Configured: {', '.join(self.servers()) or '(none — add to .hermes/mcp.json)'}"
        env = {**os.environ, **(spec.get("env") or {})}
        try:
            proc = subprocess.Popen(
                [spec["command"], *spec.get("args", [])],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, env=env, bufsize=1,
            )
        except OSError as exc:
            return f"Failed to spawn '{name}': {exc}"
        conn = _Connection(name, proc)
        try:
            result = conn.rpc("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "hermes-ultimate", "version": "1.0"},
            }, timeout=timeout)
            conn.notify("notifications/initialized")
        except (TimeoutError, RuntimeError, OSError) as exc:
            conn.close()
            return f"Handshake with '{name}' failed: {exc}"
        self._connections[name] = conn
        server_info = result.get("serverInfo", {})
        return f"Connected to '{name}' ({server_info.get('name', '?')} {server_info.get('version', '')})".strip()

    def _conn(self, name: str) -> Optional[_Connection]:
        conn = self._connections.get(name)
        if conn and conn.proc.poll() is None:
            return conn
        # auto-connect on first use
        note = self.connect(name)
        conn = self._connections.get(name)
        if not conn:
            raise RuntimeError(note)
        return conn

    # ── MCP operations ────────────────────────────────────────
    def list_tools(self, name: str) -> List[Dict[str, Any]]:
        result = self._conn(name).rpc("tools/list")
        return [
            {"name": t.get("name"), "description": t.get("description", ""), "inputSchema": t.get("inputSchema", {})}
            for t in result.get("tools", [])
        ]

    def call_tool(self, name: str, tool: str, arguments: Optional[dict] = None) -> str:
        result = self._conn(name).rpc("tools/call", {"name": tool, "arguments": arguments or {}}, timeout=60.0)
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item))
        if result.get("isError"):
            return "MCP tool error: " + ("\n".join(parts) or "(no detail)")
        return "\n".join(parts) or json.dumps(result)

    def close_all(self):
        for conn in self._connections.values():
            conn.close()
        self._connections = {}

    def status(self) -> Dict[str, str]:
        out = {}
        for name in self.servers():
            conn = self._connections.get(name)
            out[name] = "connected" if conn and conn.proc.poll() is None else "configured"
        return out
