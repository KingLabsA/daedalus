"""Persistent tool-event log — the substrate dream/distill mine across sessions."""

import sqlite3
from datetime import datetime
from typing import Any


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class EventLog:
    def __init__(self, db_path: str = "hermes_ultimate.db", session_id: str = "default"):
        self.db_path = db_path
        self.session_id = session_id
        self._pending: dict[str, dict[str, Any]] = {}
        self._registered = []
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    args_sig TEXT NOT NULL DEFAULT '',
                    ok INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT
                )""")

    # ── Hook lifecycle ────────────────────────────────────────
    def attach(self, hook_manager):
        pairs = [("pre_tool", self._on_pre_tool), ("post_tool", self._on_post_tool)]
        for event, handler in pairs:
            hook_manager.register(event, handler)
        self._registered = [(hook_manager, e, h) for e, h in pairs]

    def detach(self):
        for hook_manager, event, handler in self._registered:
            hook_manager.unregister(event, handler)
        self._registered = []

    def _on_pre_tool(self, calls: list[dict] | None = None, **kwargs):
        try:
            for call in calls or []:
                cid = call.get("id") or ""
                if cid:
                    self._pending[cid] = call
            if len(self._pending) > 200:
                for key in list(self._pending)[:-100]:
                    self._pending.pop(key, None)
        except Exception:
            pass

    def _on_post_tool(self, results: list[dict] | None = None, **kwargs):
        try:
            rows = []
            for res in results or []:
                call = self._pending.pop(res.get("id") or "", None)
                if not call:
                    continue
                output = str(res.get("result") or "")
                ok = 0 if ("Error" in output or "ToolError" in output) else 1
                args = call.get("args", {}) or {}
                sig = " ".join(f"{k}={str(args[k])[:60]}" for k in sorted(args)[:3])[:200]
                rows.append((self.session_id, call.get("name", "unknown"), sig, ok, _now()))
            if rows:
                with self._conn() as conn:
                    conn.executemany(
                        "INSERT INTO tool_events (session_id, tool, args_sig, ok, created_at) VALUES (?,?,?,?,?)",
                        rows,
                    )
        except Exception:
            pass

    # ── Mining API ────────────────────────────────────────────
    def record(self, tool: str, args_sig: str = "", ok: bool = True):
        """Direct recording (for tests or non-hook callers)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tool_events (session_id, tool, args_sig, ok, created_at) VALUES (?,?,?,?,?)",
                (self.session_id, tool, args_sig, 1 if ok else 0, _now()),
            )

    def sequences(self, max_sessions: int = 20) -> list[list[str]]:
        """Ordered tool-name sequences, one list per session (most recent sessions)."""
        with self._conn() as conn:
            sessions = [
                r[0]
                for r in conn.execute(
                    "SELECT session_id FROM tool_events GROUP BY session_id ORDER BY MAX(id) DESC LIMIT ?",
                    (max_sessions,),
                ).fetchall()
            ]
            out = []
            for sid in sessions:
                rows = conn.execute("SELECT tool FROM tool_events WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
                out.append([r[0] for r in rows])
        return out

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            return {
                "events": conn.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0],
                "sessions": conn.execute("SELECT COUNT(DISTINCT session_id) FROM tool_events").fetchone()[0],
            }
