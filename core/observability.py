"""Observability — structured JSON telemetry + in-process metrics.

Every LLM call and tool execution emits a JSON line (.hermes/telemetry.jsonl,
rotated at 10MB) and updates latency/error counters queryable via metrics().
Standalone, stdlib-only, hook-driven, never raises into the agent loop.
"""
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

MAX_LOG_BYTES = 10_000_000


class _Timer:
    __slots__ = ("count", "total", "max", "errors", "slow")

    def __init__(self):
        self.count = 0
        self.total = 0.0
        self.max = 0.0
        self.errors = 0
        self.slow = 0  # >5s

    def observe(self, seconds: float, error: bool = False):
        self.count += 1
        self.total += seconds
        self.max = max(self.max, seconds)
        if error:
            self.errors += 1
        if seconds > 5:
            self.slow += 1

    def snapshot(self) -> Dict:
        return {"count": self.count, "errors": self.errors, "slow_gt5s": self.slow,
                "avg_s": round(self.total / self.count, 3) if self.count else 0,
                "max_s": round(self.max, 3)}


class Telemetry:
    def __init__(self, root_dir: str = ".hermes"):
        self.root = Path(root_dir)
        self.path = self.root / "telemetry.jsonl"
        self._lock = threading.Lock()
        self._timers: Dict[str, _Timer] = {}
        self._counters: Dict[str, int] = {}
        self._inflight: Dict[str, float] = {}  # key -> monotonic start
        self._registered: List = []
        self.started_at = time.time()

    # ── structured event log ──────────────────────────────────
    def event(self, kind: str, **fields):
        try:
            record = {"ts": datetime.now().isoformat(timespec="milliseconds"), "kind": kind, **fields}
            line = json.dumps(record, default=str)[:4000]
            with self._lock:
                self.root.mkdir(parents=True, exist_ok=True)
                try:
                    if self.path.exists() and self.path.stat().st_size > MAX_LOG_BYTES:
                        self.path.rename(self.path.with_suffix(".jsonl.1"))
                except OSError:
                    pass
                with open(self.path, "a") as fh:
                    fh.write(line + "\n")
        except Exception:
            pass

    def count(self, name: str, n: int = 1):
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + n

    def _timer(self, name: str) -> _Timer:
        with self._lock:
            if name not in self._timers:
                self._timers[name] = _Timer()
            return self._timers[name]

    # ── hook wiring (llm + tool latency without touching the loop) ──
    def attach(self, hook_manager):
        pairs = [("pre_llm", self._pre_llm), ("post_llm", self._post_llm),
                 ("pre_tool", self._pre_tool), ("post_tool", self._post_tool),
                 ("on_error", self._on_error)]
        for event, handler in pairs:
            hook_manager.register(event, handler)
        self._registered = [(hook_manager, e, h) for e, h in pairs]

    def detach(self):
        for hm, e, h in self._registered:
            hm.unregister(e, h)
        self._registered = []

    def _pre_llm(self, **kw):
        self._inflight["llm"] = time.monotonic()

    def _post_llm(self, content=None, tool_calls=None, **kw):
        start = self._inflight.pop("llm", None)
        if start is None:
            return
        elapsed = time.monotonic() - start
        self._timer("llm_call").observe(elapsed)
        self.event("llm_call", seconds=round(elapsed, 3),
                   chars=len(str(content or "")), tool_calls=len(tool_calls or []))

    def _pre_tool(self, calls=None, **kw):
        now = time.monotonic()
        for call in calls or []:
            cid = call.get("id") or ""
            if cid:
                self._inflight[f"tool:{cid}"] = now
                self._inflight[f"toolname:{cid}"] = call.get("name", "unknown")

    def _post_tool(self, results=None, **kw):
        now = time.monotonic()
        for res in results or []:
            cid = res.get("id") or ""
            start = self._inflight.pop(f"tool:{cid}", None)
            name = self._inflight.pop(f"toolname:{cid}", "unknown")
            if start is None:
                continue
            elapsed = now - start
            output = str(res.get("result") or "")
            error = "Error" in output or "ToolError" in output
            self._timer(f"tool:{name}").observe(elapsed, error=error)
            self._timer("tool:_all").observe(elapsed, error=error)
            self.event("tool_call", tool=name, seconds=round(elapsed, 3), error=error)

    def _on_error(self, error=None, **kw):
        self.count("errors")
        self.event("error", message=str(error or "")[:500])

    # ── reporting ─────────────────────────────────────────────
    def metrics(self) -> Dict:
        with self._lock:
            timers = {k: t.snapshot() for k, t in sorted(self._timers.items())}
            counters = dict(self._counters)
        return {"uptime_s": round(time.time() - self.started_at, 1),
                "counters": counters, "latency": timers,
                "telemetry_file": str(self.path)}

    def slowest_tools(self, k: int = 5) -> List[Dict]:
        with self._lock:
            items = [(name[5:], t) for name, t in self._timers.items()
                     if name.startswith("tool:") and name != "tool:_all"]
        items.sort(key=lambda p: p[1].max, reverse=True)
        return [{"tool": n, **t.snapshot()} for n, t in items[:k]]
