"""ChangesetManager — records every agent file edit per turn for review.

Apply-then-review: tools write immediately (so the agent can verify its own
work mid-turn), while this manager snapshots old/new content. Review at file
OR hunk granularity: rejecting a hunk rebuilds the file with only that block
restored to the original. Standalone, stdlib-only, never raises into the loop.
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


def _compute_ops(old: str, new: str):
    """SequenceMatcher opcodes over keepends lines, with adjacent non-equal
    blocks merged into single hunks. Returns (ops, hunk_count) where each op is
    {tag, i1, i2, j1, j2, hunk} (hunk index or None for equal)."""
    a = old.splitlines(keepends=True)
    b = new.splitlines(keepends=True)
    ops, hunk = [], -1
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            ops.append({"tag": "equal", "i1": i1, "i2": i2, "j1": j1, "j2": j2, "hunk": None})
        elif ops and ops[-1]["hunk"] is not None:  # merge adjacent change blocks
            ops[-1].update(i2=i2, j2=j2, tag="change")
        else:
            hunk += 1
            ops.append({"tag": "change", "i1": i1, "i2": i2, "j1": j1, "j2": j2, "hunk": hunk})
    return ops, hunk + 1


class ChangesetManager:
    def __init__(self):
        self._turns: List[Dict] = []   # [{id, entries: {path: entry}}]
        self._pending: Dict[str, Dict] = {}
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
                    continue
                p = Path(pend["path"])
                new = p.read_text(errors="replace") if p.is_file() else ""
                if new == pend["old"]:
                    continue
                turn = self._current_turn()
                prior = turn["entries"].get(pend["path"])
                old = prior["old"] if prior else pend["old"]  # chain edits within a turn
                ops, n_hunks = _compute_ops(old, new)
                turn["entries"][pend["path"]] = {
                    "path": pend["path"], "tool": pend["tool"],
                    "old": old, "new": new, "ops": ops,
                    "hunks": [{"status": "applied"} for _ in range(n_hunks)],
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

    def _entry(self, cs_id: str, path: str) -> Optional[Dict]:
        turn = self._find(cs_id)
        return (turn or {}).get("entries", {}).get(path)

    # ── hunk mechanics ────────────────────────────────────────
    @staticmethod
    def _file_status(entry: Dict) -> str:
        statuses = {h["status"] for h in entry["hunks"]} or {"applied"}
        if statuses == {"reverted"}:
            return "reverted"
        if statuses == {"accepted"}:
            return "accepted"
        if "reverted" in statuses:
            return "partial"
        return "applied"

    @staticmethod
    def _hunk_diff(entry: Dict, hunk_idx: int) -> str:
        a = entry["old"].splitlines(keepends=True)
        b = entry["new"].splitlines(keepends=True)
        for op in entry["ops"]:
            if op["hunk"] == hunk_idx:
                out = [f"@@ -{op['i1'] + 1},{op['i2'] - op['i1']} +{op['j1'] + 1},{op['j2'] - op['j1']} @@"]
                out += ["-" + l.rstrip("\n") for l in a[op["i1"]:op["i2"]]]
                out += ["+" + l.rstrip("\n") for l in b[op["j1"]:op["j2"]]]
                return "\n".join(out[:200])
        return ""

    @staticmethod
    def _rebuild(entry: Dict) -> str:
        """Compose file content: kept hunks use new lines, reverted use old."""
        a = entry["old"].splitlines(keepends=True)
        b = entry["new"].splitlines(keepends=True)
        out: List[str] = []
        for op in entry["ops"]:
            if op["hunk"] is None:
                out.extend(a[op["i1"]:op["i2"]])
            elif entry["hunks"][op["hunk"]]["status"] == "reverted":
                out.extend(a[op["i1"]:op["i2"]])
            else:
                out.extend(b[op["j1"]:op["j2"]])
        return "".join(out)

    def _write_rebuilt(self, entry: Dict) -> str:
        try:
            content = self._rebuild(entry)
            p = Path(entry["path"])
            if not content and not entry["old"] and p.is_file():
                p.unlink()  # agent-created file fully reverted
                return "removed"
            p.write_text(content)
            return "written"
        except OSError as exc:
            return f"failed: {exc}"

    # ── review API ────────────────────────────────────────────
    def summary(self, cs_id: str = "") -> Dict:
        turn = self._find(cs_id) if cs_id else (self._turns[-1] if self._turns else None)
        if not turn:
            return {"id": None, "files": []}
        return {
            "id": turn["id"],
            "files": [
                {
                    "path": e["path"], "tool": e["tool"], "status": self._file_status(e),
                    "diff": _diff(e["old"], e["new"], e["path"]),
                    "hunks": [
                        {"index": i, "status": h["status"], "diff": self._hunk_diff(e, i)}
                        for i, h in enumerate(e["hunks"])
                    ],
                }
                for e in turn["entries"].values()
            ],
        }

    def list_turns(self) -> List[Dict]:
        return [{"id": t["id"], "files": len(t["entries"])} for t in self._turns if t["entries"]]

    def original(self, cs_id: str, path: str) -> Optional[str]:
        """Pre-edit content of a file in a changeset (for external diff viewers)."""
        entry = self._entry(cs_id, path)
        return entry["old"] if entry else None

    def accept(self, cs_id: str, path: str) -> str:
        entry = self._entry(cs_id, path)
        if not entry:
            return f"No entry for {path} in {cs_id}"
        for h in entry["hunks"]:
            if h["status"] == "applied":
                h["status"] = "accepted"
        return f"Accepted {path}"

    def reject(self, cs_id: str, path: str) -> str:
        entry = self._entry(cs_id, path)
        if not entry:
            return f"No entry for {path} in {cs_id}"
        if self._file_status(entry) == "reverted":
            return f"{path} already reverted"
        for h in entry["hunks"]:
            h["status"] = "reverted"
        result = self._write_rebuilt(entry)
        return f"Reverted {path}" if result in ("written", "removed") else f"Revert {result}"

    def accept_hunk(self, cs_id: str, path: str, hunk_idx: int) -> str:
        entry = self._entry(cs_id, path)
        if not entry or not (0 <= hunk_idx < len(entry["hunks"])):
            return f"No hunk {hunk_idx} for {path} in {cs_id}"
        entry["hunks"][hunk_idx]["status"] = "accepted"
        return f"Accepted hunk {hunk_idx} of {path}"

    def reject_hunk(self, cs_id: str, path: str, hunk_idx: int) -> str:
        entry = self._entry(cs_id, path)
        if not entry or not (0 <= hunk_idx < len(entry["hunks"])):
            return f"No hunk {hunk_idx} for {path} in {cs_id}"
        if entry["hunks"][hunk_idx]["status"] == "reverted":
            return f"Hunk {hunk_idx} already reverted"
        entry["hunks"][hunk_idx]["status"] = "reverted"
        result = self._write_rebuilt(entry)
        return (f"Reverted hunk {hunk_idx} of {path}"
                if result in ("written", "removed") else f"Revert {result}")


def safe_repo_path(path: str, root: str = ".") -> Optional[Path]:
    """Resolve a user-supplied path, refusing escapes outside the project root."""
    try:
        root_r = Path(root).resolve()
        p = (root_r / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        return p if p == root_r or root_r in p.parents else None
    except OSError:
        return None
