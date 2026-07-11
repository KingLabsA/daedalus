"""Failure immune system: remembers how the agent got burned, warns before it repeats."""

from typing import Any

from .store import MemoryStore

ERROR_MARKERS = ("Error", "ToolError", "Traceback", "FAILED", "failed:")


def _signature(tool: str, args: dict[str, Any]) -> str:
    """Stable, compact signature for a tool call: tool name + key argument values."""
    parts = [tool]
    for key in sorted(args)[:4]:
        val = str(args[key])[:80]
        parts.append(f"{key}={val}")
    return " ".join(parts)[:200]


class ImmuneSystem:
    def __init__(self, store: MemoryStore):
        self.store = store
        self._pending: dict[str, dict[str, Any]] = {}

    def observe_calls(self, calls: list[dict[str, Any]]):
        for call in calls or []:
            cid = call.get("id") or ""
            if cid:
                self._pending[cid] = call
        # don't let the cache grow unbounded across a long session
        if len(self._pending) > 200:
            for key in list(self._pending)[:-100]:
                self._pending.pop(key, None)

    def observe_results(self, results: list[dict[str, Any]]) -> int:
        recorded = 0
        for res in results or []:
            output = str(res.get("result") or "")
            if not any(marker in output for marker in ERROR_MARKERS):
                continue
            call = self._pending.pop(res.get("id") or "", None)
            tool = call.get("name", "unknown") if call else "unknown"
            args = call.get("args", {}) if call else {}
            self.store.record_failure(tool, _signature(tool, args), output)
            recorded += 1
        return recorded

    def antibodies_for(self, messages: list[dict], k: int = 3) -> str:
        """Render a warning block of past failures relevant to the current intent."""
        intent = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                intent = str(msg.get("content") or "")[:500]
                break
        if not intent:
            return ""
        hits = self.store.find_antibodies(intent, k=k)
        if not hits:
            return ""
        lines = ["[ANTIBODIES] Past failures relevant to this request — do not repeat them:"]
        for hit in hits:
            error_head = hit["error"].splitlines()[0][:160] if hit["error"] else ""
            line = f"- {hit['tool']} ({hit['hits']}x): {hit['signature'][:120]} -> {error_head}"
            if hit.get("remedy"):
                line += f" | remedy: {hit['remedy'][:160]}"
            lines.append(line)
        return "\n".join(lines)
