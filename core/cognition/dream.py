"""Dream — consolidate session experience into persistent memory (auto /dream)."""
import difflib
import json
import re
from typing import Callable, Dict, List, Optional

CORRECTION_MARKERS = (
    "don't", "do not", "instead", "always", "never", "prefer",
    "remember", "wrong", "actually", "stop doing", "use ",
)
SIMILARITY_CUTOFF = 0.75
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


class Dreamer:
    def __init__(self, store, llm_fn: Optional[Callable[[str], str]] = None, max_memories: int = 500):
        self.store = store  # core.context.MemoryStore
        self.llm_fn = llm_fn
        self.max_memories = max_memories

    # ── Extraction ────────────────────────────────────────────
    def _heuristic_candidates(self, sessions: List[List[Dict]]) -> List[Dict]:
        candidates = []
        for messages in sessions:
            for msg in messages or []:
                if msg.get("role") != "user":
                    continue
                content = str(msg.get("content") or "").strip()
                if not (10 <= len(content) <= 300) or content.startswith(("[", "/")):
                    continue
                lowered = content.lower()
                if lowered.startswith("remember"):
                    candidates.append({"content": content, "kind": "note", "importance": 0.8})
                elif any(marker in lowered for marker in CORRECTION_MARKERS):
                    candidates.append({"content": content, "kind": "preference", "importance": 0.6})
        return candidates

    def _llm_candidates(self, sessions: List[List[Dict]]) -> List[Dict]:
        if not self.llm_fn:
            return []
        tails = []
        for messages in sessions[:5]:
            tail = "\n".join(
                f"{m.get('role')}: {str(m.get('content') or '')[:250]}" for m in (messages or [])[-20:]
            )
            if tail:
                tails.append(tail)
        if not tails:
            return []
        prompt = (
            "You are consolidating an AI coding agent's session logs into long-term memory.\n"
            "Extract ONLY durable, reusable knowledge: project facts, architecture decisions, "
            "user preferences, hard-won lessons. Skip transient task details.\n"
            'Reply with a JSON array only: [{"content": "...", "kind": "project|decision|note|preference", '
            '"importance": 0.0-1.0}]. Max 8 items. Empty array if nothing durable.\n\n'
            + "\n\n---\n\n".join(tails)
        )
        try:
            raw = str(self.llm_fn(prompt))
            match = _JSON_ARRAY_RE.search(raw)
            if not match:
                return []
            items = json.loads(match.group(0))
            out = []
            for item in items[:8]:
                content = str(item.get("content", "")).strip()
                if 5 <= len(content) <= 500:
                    out.append({
                        "content": content,
                        "kind": str(item.get("kind", "note")),
                        "importance": float(item.get("importance", 0.5)),
                    })
            return out
        except Exception:
            return []

    def _is_duplicate(self, content: str) -> bool:
        hits = self.store.search_memories(content, k=1)
        if not hits:
            return False
        ratio = difflib.SequenceMatcher(None, content.lower(), hits[0]["content"].lower()).ratio()
        return ratio >= SIMILARITY_CUTOFF

    def _prune(self) -> int:
        memories = self.store.list_memories(limit=100000)
        overflow = len(memories) - self.max_memories
        if overflow <= 0:
            return 0
        # list_memories is ordered best-first; evict from the tail
        for mem in memories[-overflow:]:
            self.store.delete_memory(mem["id"])
        return overflow

    # ── Public API ────────────────────────────────────────────
    def dream(self, sessions: List[List[Dict]], use_llm: bool = True) -> Dict:
        candidates = self._heuristic_candidates(sessions)
        if use_llm:
            candidates += self._llm_candidates(sessions)
        added, dupes = 0, 0
        seen_this_run: List[str] = []
        for cand in candidates:
            content = cand["content"]
            if any(
                difflib.SequenceMatcher(None, content.lower(), prev.lower()).ratio() >= SIMILARITY_CUTOFF
                for prev in seen_this_run
            ) or self._is_duplicate(content):
                dupes += 1
                continue
            self.store.add_memory(content, cand.get("kind", "note"), cand.get("importance", 0.5))
            seen_this_run.append(content)
            added += 1
        pruned = self._prune()
        return {"candidates": len(candidates), "added": added, "duplicates": dupes, "pruned": pruned}
