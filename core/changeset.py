"""ChangesetManager — records every agent file edit per turn for review.

Apply-then-review: tools write immediately (so the agent can verify its own
work mid-turn), while this manager snapshots old/new content. Reject restores
the original bytes; Accept marks the entry reviewed. Standalone, stdlib-only,
never raises into the agent loop.
"""
import difflib
from pathlib import Path
from typing import Dict, List, Optional

WRITE_TOOLS = {"write_file", "append_file", "edit_file_line"}
MAX_TURNS = 20
MAX_SNAPSHOT_BYTES = 2_000_000


def _diff(old: str, new: str, path: str) -> str:
    lines = list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=3))
    return "\n".join(lines[:400])


class ChangesetManager:
    def __init__(self):
        self._turns: List[Dict] = []   # [{id, entries: {path: entry}}]
        self._pending: Dict[str, Dict] = {}  # call_id -> {tool, path, old}
        self._counter = 0
        self._registered = []

    # ── hooks ─────────────────────────────────────────────────
    def attach(self, hook_manager):
        pairs = [("pre_tool", self._on_pre_tool), ("post_tool", self._on_post_tool)]
        for event, handler in pairs:
            hook_manager.register(event, handler)
        self._registered = [(hook_manager, e, h) for e, h in pairs]

    def detach(self):
        for hm, e, h in self._registered:
            hm.unregister(e, h)
        self._registered = []

    def _on_pre_tool(self, calls: Optional[List[Dict]] = None, **kw):
        try:
            for call in calls or []:
                if call.get("name") not in WRITE_TOOLS:
                    continue
                path = (call.get("args") or {}).get("filepath", "")
                cid = call.get("id") or ""
                if not path or not cid:
                    continue
                p = Path(path)
                old = ""
                if p.is_file() and p.stat().st_size <= MAX_SNAPSHOT_BYTES:
                    old = p.read_text(errors="replace")
                self._pending[cid] = {"tool": call["name"], "path": path, "old": old}
        except Exception:
            pass

    def _on_post_tool(self, results: Optional[List[Dict]] = None, **kw):
        try:
            for res in results or []:
                pend = self._pending.pop(res.get("id") or "", None)
                if not pend:
                    continue
                output = str(res.get("result") or "")
                if "Error" in output or "ToolError" in output:
                    continue  # failed write: nothing applied
                p = Path(pend["path"])
                new = p.read_text(errors="replace") if p.is_file() else ""
                if new == pend["old"]:
                    continue
                turn = self._current_turn()
                turn["entries"][pend["path"]] = {
                    "path": pend["path"], "tool": pend["tool"],
                    "old": pend["old"], "new": new, "status": "applied",
                }
        except Exception:
            pass

    # ── turns ─────────────────────────────────────────────────
    def begin_turn(self) -> str:
        self._counter += 1
        cs_id = f"cs_{self._counter}"
        self._turns.append({"id": cs_id, "entries": {}})
        self._turns = self._turns[-MAX_TURNS:]
        self._pending.clear()
        return cs_id

    def _current_turn(self) -> Dict:
        if not self._turns:
            self.begin_turn()
        return self._turns[-1]

    def _find(self, cs_id: str) -> Optional[Dict]:
        return next((t for t in self._turns if t["id"] == cs_id), None)

    # ── review API ────────────────────────────────────────────
    def summary(self, cs_id: str = "") -> Dict:
        turn = self._find(cs_id) if cs_id else (self._turns[-1] if self._turns else None)
        if not turn:
            return {"id": None, "files": []}
        return {
            "id": turn["id"],
            "files": [
                {"path": e["path"], "tool": e["tool"], "status": e["status"],
                 "diff": _diff(e["old"], e["new"], e["path"])}
                for e in turn["entries"].values()
            ],
        }

    def list_turns(self) -> List[Dict]:
        return [{"id": t["id"], "files": len(t["entries"])} for t in self._turns if t["entries"]]

    def accept(self, cs_id: str, path: str) -> str:
        turn = self._find(cs_id)
        entry = (turn or {}).get("entries", {}).get(path)
        if not entry:
            return f"No entry for {path} in {cs_id}"
        entry["status"] = "accepted"
        return f"Accepted {path}"

    def reject(self, cs_id: str, path: str) -> str:
        turn = self._find(cs_id)
        entry = (turn or {}).get("entries", {}).get(path)
        if not entry:
            return f"No entry for {path} in {cs_id}"
        if entry["status"] == "reverted":
            return f"{path} already reverted"
        try:
            p = Path(entry["path"])
            if entry["old"]:
                p.write_text(entry["old"])
            elif p.is_file():
                p.unlink()  # file was created by the agent; reject removes it
            entry["status"] = "reverted"
            return f"Reverted {path}"
        except OSError as exc:
            return f"Revert failed: {exc}"


def safe_repo_path(path: str, root: str = ".") -> Optional[Path]:
    """Resolve a user-supplied path, refusing escapes outside the project root."""
    try:
        root_r = Path(root).resolve()
        p = (root_r / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        return p if p == root_r or root_r in p.parents else None
    except OSError:
        return None
