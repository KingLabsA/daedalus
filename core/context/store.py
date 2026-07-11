"""SQLite-backed persistent memory with FTS5 search, checkpoints, and failure records."""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

VALID_KINDS = ("project", "decision", "note", "preference")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fts_query(text: str) -> str:
    """Sanitize arbitrary text into a safe FTS5 OR-query of quoted tokens."""
    tokens = re.findall(r"[A-Za-z0-9_./-]{2,}", text)[:24]
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"' for t in tokens)


class MemoryStore:
    def __init__(self, db_path: str = "hermes_ultimate.db", root_dir: str = ".hermes/memory"):
        self.db_path = db_path
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS mem_entries(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL DEFAULT 'project',
                content TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5,
                uses INTEGER NOT NULL DEFAULT 0,
                created_at TEXT, last_used TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
                content, content='mem_entries', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON mem_entries BEGIN
                INSERT INTO mem_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON mem_entries BEGIN
                INSERT INTO mem_fts(mem_fts, rowid, content) VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE OF content ON mem_entries BEGIN
                INSERT INTO mem_fts(mem_fts, rowid, content) VALUES ('delete', old.id, old.content);
                INSERT INTO mem_fts(rowid, content) VALUES (new.id, new.content);
            END;

            CREATE TABLE IF NOT EXISTS ctx_checkpoints(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS failures(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool TEXT NOT NULL,
                signature TEXT NOT NULL,
                error TEXT NOT NULL,
                remedy TEXT NOT NULL DEFAULT '',
                hits INTEGER NOT NULL DEFAULT 1,
                created_at TEXT, last_seen TEXT,
                UNIQUE(tool, signature)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fail_fts USING fts5(
                signature, error, remedy, content='failures', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS fail_ai AFTER INSERT ON failures BEGIN
                INSERT INTO fail_fts(rowid, signature, error, remedy)
                VALUES (new.id, new.signature, new.error, new.remedy);
            END;
            CREATE TRIGGER IF NOT EXISTS fail_ad AFTER DELETE ON failures BEGIN
                INSERT INTO fail_fts(fail_fts, rowid, signature, error, remedy)
                VALUES ('delete', old.id, old.signature, old.error, old.remedy);
            END;
            CREATE TRIGGER IF NOT EXISTS fail_au AFTER UPDATE OF signature, error, remedy ON failures BEGIN
                INSERT INTO fail_fts(fail_fts, rowid, signature, error, remedy)
                VALUES ('delete', old.id, old.signature, old.error, old.remedy);
                INSERT INTO fail_fts(rowid, signature, error, remedy)
                VALUES (new.id, new.signature, new.error, new.remedy);
            END;
            """)

    # ── Memories ──────────────────────────────────────────────
    def add_memory(self, content: str, kind: str = "project", importance: float = 0.5) -> int:
        kind = kind if kind in VALID_KINDS else "note"
        importance = max(0.0, min(1.0, float(importance)))
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO mem_entries (kind, content, importance, created_at, last_used) VALUES (?,?,?,?,?)",
                (kind, content.strip(), importance, _now(), _now()),
            )
            mem_id = cur.lastrowid
        self._render_memory_md()
        return mem_id

    def delete_memory(self, mem_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM mem_entries WHERE id = ?", (mem_id,))
        self._render_memory_md()
        return cur.rowcount > 0

    def search_memories(self, query: str, k: int = 5, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        q = _fts_query(query)
        kind_filter = ""
        params: list[Any] = [q]
        if kinds:
            kind_filter = f" AND m.kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        params.append(max(1, k))
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT m.*, bm25(mem_fts) AS rank FROM mem_fts
                    JOIN mem_entries m ON m.id = mem_fts.rowid
                    WHERE mem_fts MATCH ?{kind_filter}
                    ORDER BY bm25(mem_fts) * (0.5 + m.importance) ASC, m.last_used DESC
                    LIMIT ?""",
                params,
            ).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                conn.execute(
                    f"UPDATE mem_entries SET uses = uses + 1, last_used = ? WHERE id IN ({','.join('?' * len(ids))})",
                    [_now(), *ids],
                )
        return [dict(r) for r in rows]

    def list_memories(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM mem_entries ORDER BY importance DESC, last_used DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── Checkpoints ───────────────────────────────────────────
    def write_checkpoint(self, session_id: str, data: dict[str, Any]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO ctx_checkpoints (session_id, data, created_at) VALUES (?,?,?)",
                (session_id, json.dumps(data), _now()),
            )
            cp_id = cur.lastrowid
        self._render_checkpoint_md(data)
        return cp_id

    def latest_checkpoint(self, session_id: str = "") -> dict[str, Any] | None:
        with self._conn() as conn:
            if session_id:
                row = conn.execute(
                    "SELECT data FROM ctx_checkpoints WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT data FROM ctx_checkpoints ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["data"]) if row else None

    # ── Failures (immune system) ──────────────────────────────
    def record_failure(self, tool: str, signature: str, error: str, remedy: str = "") -> int:
        error = error[:1000]
        with self._conn() as conn:
            row = conn.execute("SELECT id, hits FROM failures WHERE tool = ? AND signature = ?", (tool, signature)).fetchone()
            if row:
                conn.execute(
                    "UPDATE failures SET hits = hits + 1, error = ?, last_seen = ?, remedy = CASE WHEN ? != '' THEN ? ELSE remedy END WHERE id = ?",
                    (error, _now(), remedy, remedy, row["id"]),
                )
                return row["id"]
            cur = conn.execute(
                "INSERT INTO failures (tool, signature, error, remedy, created_at, last_seen) VALUES (?,?,?,?,?,?)",
                (tool, signature, error, remedy, _now(), _now()),
            )
            return cur.lastrowid

    def find_antibodies(self, query: str, k: int = 3) -> list[dict[str, Any]]:
        q = _fts_query(query)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT f.*, bm25(fail_fts) AS rank FROM fail_fts
                   JOIN failures f ON f.id = fail_fts.rowid
                   WHERE fail_fts MATCH ?
                   ORDER BY bm25(fail_fts) ASC, f.hits DESC LIMIT ?""",
                (q, max(1, k)),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            return {
                "memories": conn.execute("SELECT COUNT(*) FROM mem_entries").fetchone()[0],
                "checkpoints": conn.execute("SELECT COUNT(*) FROM ctx_checkpoints").fetchone()[0],
                "failures": conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0],
            }

    # ── Human-readable mirrors ────────────────────────────────
    def _render_memory_md(self):
        try:
            lines = ["# Hermes Project Memory", ""]
            for m in self.list_memories(limit=100):
                lines.append(f"- **[{m['kind']}]** (imp {m['importance']:.1f}) {m['content']}")
            (self.root / "MEMORY.md").write_text("\n".join(lines) + "\n")
        except OSError:
            pass

    def _render_checkpoint_md(self, data: dict[str, Any]):
        try:
            lines = ["# Session Checkpoint", ""]
            for key, val in data.items():
                if isinstance(val, (list, dict)):
                    val = json.dumps(val, indent=1)
                lines.append(f"## {key}\n{val}\n")
            (self.root / "checkpoint.md").write_text("\n".join(lines))
        except OSError:
            pass
