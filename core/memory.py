"""Memory — session store, context compression, plugin marketplace.

Standalone: imports nothing from agent_ultimate. Provider calls via ProviderRouter.
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.providers import LLM_PROVIDER, ProviderRouter

DB_FILE = os.getenv("DB_FILE", "hermes_ultimate.db")
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", ".hermes/skills"))
SKILLS_DIR.mkdir(parents=True, exist_ok=True)
PLUGINS_DIR = Path(os.getenv("PLUGINS_DIR", "plugins"))
PLUGINS_DIR.mkdir(parents=True, exist_ok=True)


# ============== SESSION STORE ==============
class SessionStore:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, messages TEXT, updated_at TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS skills (name TEXT PRIMARY KEY, content TEXT, created_at TEXT)")

    def load(self, session_id: str) -> Optional[list[dict]]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT messages FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, session_id: str, messages: list[dict]):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO sessions (session_id, messages, updated_at) VALUES (?, ?, ?)", (session_id, json.dumps(messages), now))

    def list_sessions(self) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [r[0] for r in conn.execute("SELECT session_id FROM sessions ORDER BY updated_at DESC").fetchall()]


# ============== CONTEXT COMPRESSION ==============
def compress_messages(messages: list[dict], keep_recent: int = 5, provider: str = "") -> list[dict]:
    if len(messages) <= 20:
        return messages
    system_msg = messages.pop(0) if messages and messages[0]["role"] == "system" else None
    middle = messages[3:-keep_recent]
    recent = messages[-keep_recent:]
    if not middle:
        return messages
    prompt = "Summarize this conversation concisely:\n" + "\n".join([f"{m['role']}: {str(m.get('content', ''))[:200]}" for m in middle])
    try:
        p = provider or LLM_PROVIDER
        summary = ProviderRouter.call([{"role": "user", "content": prompt}], [], p).content
    except Exception:
        summary = "[Conversation summarized]"
    compressed = messages[:3] + [{"role": "user", "content": f"[Summary]: {summary}"}] + recent
    if system_msg:
        compressed.insert(0, system_msg)
    return compressed


# ============== PLUGIN MARKETPLACE ==============
class PluginMarketplace:
    PLUGIN_MANIFEST_FIELDS = {"name", "version", "description", "author", "tools", "min_agent_version"}

    @staticmethod
    def validate_manifest(data: dict) -> tuple[bool, str]:
        missing = PluginMarketplace.PLUGIN_MANIFEST_FIELDS - set(data.keys())
        if missing:
            return False, f"Missing fields: {missing}"
        return True, "valid"

    @staticmethod
    def discover_local() -> list[dict]:
        plugins = []
        if not PLUGINS_DIR.exists():
            return plugins
        for plugin_dir in PLUGINS_DIR.iterdir():
            if plugin_dir.is_dir():
                manifest_path = plugin_dir / "plugin.json"
                if manifest_path.exists():
                    try:
                        data = json.loads(manifest_path.read_text())
                        data["path"] = str(plugin_dir)
                        plugins.append(data)
                    except Exception:
                        pass
        return plugins

    @staticmethod
    def list_remote() -> list[dict]:
        try:
            import requests

            resp = requests.get(os.getenv("PLUGIN_REGISTRY_URL", "https://hermes-plugins.fake"), timeout=5)
            return resp.json().get("plugins", [])
        except Exception:
            return []
