"""Distill — mine repeated tool workflows from the event log into reusable skills."""

import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from .events import EventLog


class Distiller:
    def __init__(
        self,
        events: EventLog,
        save_skill_fn: Callable[[str, str, list], None],
        llm_fn: Callable[[str], str] | None = None,
        min_support: int = 3,
        min_len: int = 2,
        max_len: int = 5,
        max_new_per_run: int = 3,
    ):
        self.events = events
        self.save_skill_fn = save_skill_fn
        self.llm_fn = llm_fn
        self.min_support = min_support
        self.min_len = min_len
        self.max_len = max_len
        self.max_new_per_run = max_new_per_run
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.events.db_path)

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS distilled_skills(name TEXT PRIMARY KEY, support INTEGER, created_at TEXT)")

    def _already_distilled(self, name: str) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM distilled_skills WHERE name = ?", (name,)).fetchone() is not None

    def _mark(self, name: str, support: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO distilled_skills (name, support, created_at) VALUES (?,?,?)",
                (name, support, datetime.now().isoformat(timespec="seconds")),
            )

    @staticmethod
    def _skill_name(gram: tuple[str, ...]) -> str:
        raw = "wf_" + "_".join(gram)
        return re.sub(r"[^A-Za-z0-9_]+", "_", raw)[:60]

    def _mine(self) -> list[tuple[tuple[str, ...], int]]:
        counts: Counter = Counter()
        for seq in self.events.sequences():
            for n in range(self.min_len, self.max_len + 1):
                for i in range(len(seq) - n + 1):
                    gram = tuple(seq[i : i + n])
                    if len(set(gram)) < 2:  # skip single-tool loops
                        continue
                    counts[gram] += 1
        candidates = [(g, c) for g, c in counts.items() if c >= self.min_support]
        # prefer longer, more frequent workflows
        candidates.sort(key=lambda gc: (len(gc[0]), gc[1]), reverse=True)
        return candidates

    def _describe(self, gram: tuple[str, ...], support: int) -> str:
        default = f"Auto-distilled workflow: {' -> '.join(gram)} (observed {support}x)"
        if not self.llm_fn:
            return default
        try:
            desc = str(
                self.llm_fn(
                    f"In one sentence, describe the purpose of this repeated coding-agent tool workflow: {' -> '.join(gram)}. Reply with the sentence only."
                )
            ).strip()
            return desc[:200] if desc else default
        except Exception:
            return default

    def distill(self) -> dict:
        new_skills = []
        subsumed = set()
        mined = self._mine()
        for gram, support in mined:
            if len(new_skills) >= self.max_new_per_run:
                break
            name = self._skill_name(gram)
            if self._already_distilled(name) or name in subsumed:
                continue
            # grams fully contained in a workflow we just saved are marked as
            # handled too, so they never resurface on a later run
            if any(" ".join(gram) in " ".join(saved) for saved, _ in new_skills):
                self._mark(name, support)
                subsumed.add(name)
                continue
            desc = self._describe(gram, support)
            try:
                self.save_skill_fn(name, desc, [{"tool": t} for t in gram])
            except Exception:
                continue
            self._mark(name, support)
            new_skills.append((gram, name))
        return {
            "mined": len(mined),
            "new_skills": [name for _, name in new_skills],
        }
