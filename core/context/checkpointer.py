"""Builds structured session checkpoints from message history."""
import json
import re
from collections import Counter
from typing import Callable, Dict, List, Optional

from .store import MemoryStore

_PATH_RE = re.compile(r"(?:^|[\s\"'(=])((?:\.{0,2}/)?[\w./-]+\.(?:py|ts|tsx|js|jsx|json|md|rs|go|java|rb|toml|yaml|yml|css|html))\b")


class Checkpointer:
    def __init__(self, store: MemoryStore, summarize_fn: Optional[Callable[[str], str]] = None):
        self.store = store
        self.summarize_fn = summarize_fn

    def build(self, messages: List[Dict]) -> Dict:
        goal = ""
        last_request = ""
        last_assistant = ""
        files: List[str] = []
        tools: Counter = Counter()
        for msg in messages:
            role = msg.get("role", "")
            content = str(msg.get("content") or "")
            if role == "user":
                if not goal:
                    goal = content[:500]
                last_request = content[:500]
            elif role == "assistant" and content:
                last_assistant = content[:800]
            elif role == "tool":
                files.extend(_PATH_RE.findall(content[:2000]))
        # tool usage is inferable from tool_call ids only loosely; count tool msgs
        tools["tool_results"] = sum(1 for m in messages if m.get("role") == "tool")
        seen = []
        for f in files:
            if f not in seen:
                seen.append(f)
        checkpoint = {
            "goal": goal,
            "last_request": last_request,
            "last_assistant": last_assistant,
            "files_touched": seen[:20],
            "tools_used": dict(tools),
            "message_count": len(messages),
            "summary": "",
        }
        if self.summarize_fn:
            try:
                transcript = "\n".join(
                    f"{m.get('role')}: {str(m.get('content') or '')[:300]}" for m in messages[-40:]
                )
                checkpoint["summary"] = str(
                    self.summarize_fn(
                        "Summarize the current state of this coding session in <150 words. "
                        "Include: what was being done, key decisions, and immediate next step.\n\n" + transcript
                    )
                )[:2000]
            except Exception:
                checkpoint["summary"] = ""
        return checkpoint

    def save(self, session_id: str, messages: List[Dict]) -> Dict:
        checkpoint = self.build(messages)
        self.store.write_checkpoint(session_id, checkpoint)
        return checkpoint

    @staticmethod
    def render(checkpoint: Dict) -> str:
        parts = ["[SESSION CHECKPOINT]"]
        for key in ("goal", "last_request", "summary", "last_assistant"):
            if checkpoint.get(key):
                parts.append(f"{key}: {checkpoint[key]}")
        if checkpoint.get("files_touched"):
            parts.append("files_touched: " + ", ".join(checkpoint["files_touched"]))
        return "\n".join(parts)
