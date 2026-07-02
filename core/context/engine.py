"""ContextEngine — wires memory, budgeting, checkpoints, and immunity into the agent
via lifecycle hooks. Never raises into the agent loop.
"""
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .budgeter import TokenBudgeter, estimate_tokens
from .checkpointer import Checkpointer
from .immune import ImmuneSystem
from .store import MemoryStore

CTX_BEGIN = "<!--HERMES:CTX:BEGIN-->"
CTX_END = "<!--HERMES:CTX:END-->"
_CTX_RE = re.compile(re.escape(CTX_BEGIN) + r".*?" + re.escape(CTX_END), re.DOTALL)

RECENT_TAIL = 8


class ContextEngine:
    def __init__(
        self,
        db_path: str = "hermes_ultimate.db",
        session_id: str = "",
        root_dir: str = ".hermes/memory",
        max_context_tokens: int = 0,
        summarize_fn: Optional[Callable[[str], str]] = None,
    ):
        self.store = MemoryStore(db_path, root_dir)
        self.budgeter = TokenBudgeter(max_context_tokens)
        self.checkpointer = Checkpointer(self.store, summarize_fn)
        self.immune = ImmuneSystem(self.store)
        self.session_id = session_id or "default"
        self.root = Path(root_dir)
        self._resume_shown = False
        self._live_messages: Optional[List[Dict]] = None
        self._registered = []

    # ── Hook lifecycle ────────────────────────────────────────
    def attach(self, hook_manager):
        pairs = [
            ("pre_llm", self._on_pre_llm),
            ("pre_tool", self._on_pre_tool),
            ("post_tool", self._on_post_tool),
            ("on_stop", self._on_stop),
        ]
        for event, handler in pairs:
            hook_manager.register(event, handler)
        self._registered = [(hook_manager, event, handler) for event, handler in pairs]

    def detach(self):
        for hook_manager, event, handler in self._registered:
            hook_manager.unregister(event, handler)
        self._registered = []

    def _log_error(self, where: str, exc: Exception):
        try:
            with open(self.root / "engine_errors.log", "a") as fh:
                fh.write(f"{datetime.now().isoformat()} [{where}] {exc}\n{traceback.format_exc()}\n")
        except OSError:
            pass

    # ── Handlers (never raise) ────────────────────────────────
    def _on_pre_llm(self, messages: Optional[List[Dict]] = None, **kwargs):
        if not isinstance(messages, list) or not messages:
            return
        try:
            self._live_messages = messages
            self._inject(messages)
            if self.budgeter.over_budget(messages):
                self._reconstruct(messages)
        except Exception as exc:
            self._log_error("pre_llm", exc)

    def _on_pre_tool(self, calls: Optional[List[Dict]] = None, **kwargs):
        try:
            self.immune.observe_calls(calls or [])
        except Exception as exc:
            self._log_error("pre_tool", exc)

    def _on_post_tool(self, results: Optional[List[Dict]] = None, **kwargs):
        try:
            self.immune.observe_results(results or [])
        except Exception as exc:
            self._log_error("post_tool", exc)

    def _on_stop(self, **kwargs):
        try:
            if self._live_messages and len(self._live_messages) >= 3:
                self.checkpointer.save(self.session_id, self._live_messages)
        except Exception as exc:
            self._log_error("on_stop", exc)

    # ── Context injection ─────────────────────────────────────
    def _build_block(self, messages: List[Dict]) -> str:
        alloc = self.budgeter.allocate(
            {"memory": 3.0, "antibodies": 2.0, "checkpoint": 2.0},
            total=min(2400, self.budgeter.budget // 8),
        )
        sections: List[str] = []

        intent = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                intent = str(msg.get("content") or "")[:500]
                break
        if intent:
            memories = self.store.search_memories(intent, k=5)
            if memories:
                mem_lines = ["[PROJECT MEMORY] Relevant knowledge from past sessions:"]
                mem_lines += [f"- ({m['kind']}) {m['content']}" for m in memories]
                sections.append(self.budgeter.clip("\n".join(mem_lines), alloc["memory"]))

        antibodies = self.immune.antibodies_for(messages)
        if antibodies:
            sections.append(self.budgeter.clip(antibodies, alloc["antibodies"]))

        if not self._resume_shown and len(messages) <= 3:
            checkpoint = self.store.latest_checkpoint()
            if checkpoint:
                sections.append(
                    self.budgeter.clip(
                        "[RESUME] Last session checkpoint:\n" + Checkpointer.render(checkpoint),
                        alloc["checkpoint"],
                    )
                )
            self._resume_shown = True

        return "\n\n".join(s for s in sections if s.strip())

    def _inject(self, messages: List[Dict]):
        block = self._build_block(messages)
        wrapped = f"{CTX_BEGIN}\n{block}\n{CTX_END}" if block else ""
        if messages[0].get("role") == "system":
            base = str(messages[0].get("content") or "")
            if CTX_BEGIN in base:
                base = _CTX_RE.sub("", base).rstrip()
            if wrapped:
                base = base + "\n\n" + wrapped
            messages[0]["content"] = base
        elif wrapped:
            messages.insert(0, {"role": "system", "content": wrapped})

    # ── Context reconstruction ────────────────────────────────
    def _reconstruct(self, messages: List[Dict]):
        checkpoint = self.checkpointer.save(self.session_id, messages)
        system = messages[0] if messages[0].get("role") == "system" else None
        tail = messages[-RECENT_TAIL:]
        while tail and tail[0].get("role") == "tool":
            tail = tail[1:]
        restore = {
            "role": "user",
            "content": "[CONTEXT RESTORED FROM CHECKPOINT — earlier conversation was archived]\n"
            + Checkpointer.render(checkpoint),
        }
        rebuilt = ([system] if system else []) + [restore] + [m for m in tail if m is not system]
        messages[:] = rebuilt

    # ── Public API ────────────────────────────────────────────
    def remember(self, content: str, kind: str = "project", importance: float = 0.7) -> str:
        mem_id = self.store.add_memory(content, kind, importance)
        return f"Remembered #{mem_id} [{kind}] {content[:80]}"

    def recall(self, query: str, k: int = 5) -> List[Dict]:
        return self.store.search_memories(query, k=k)

    def stats(self) -> Dict:
        data = self.store.stats()
        data["max_context_tokens"] = self.budgeter.max_context_tokens
        data["session_id"] = self.session_id
        return data
