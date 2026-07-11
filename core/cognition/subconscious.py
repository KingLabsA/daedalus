"""Subconscious — sleep-time compute. Hermes keeps thinking while the user is away:
consolidates memory (dream) and mines workflows into skills (distill) during idle time.
"""

import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


class Subconscious:
    def __init__(
        self,
        dreamer=None,
        distiller=None,
        session_loader: Callable[[int], list[list[dict]]] | None = None,
        root_dir: str = ".hermes/memory",
        idle_seconds: float = 180.0,
        poll_interval: float = 15.0,
        max_cycles_per_hour: int = 4,
        enabled: bool | None = None,
        use_llm: bool | None = None,
    ):
        self.dreamer = dreamer
        self.distiller = distiller
        self.session_loader = session_loader
        self.root = Path(root_dir)
        self.idle_seconds = idle_seconds
        self.poll_interval = poll_interval
        self.max_cycles_per_hour = max_cycles_per_hour
        if enabled is None:
            enabled = os.getenv("HERMES_SUBCONSCIOUS", "on").lower() not in ("off", "0", "false")
        self.enabled = enabled
        if use_llm is None:
            use_llm = os.getenv("HERMES_SUBCONSCIOUS_LLM", "off").lower() in ("on", "1", "true")
        self.use_llm = use_llm

        self._lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._cycled_since_activity = False
        self._cycle_times: list[float] = []
        self._reports: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._registered = []

    # ── Activity tracking ─────────────────────────────────────
    def poke(self, **kwargs):
        with self._lock:
            self._last_activity = time.monotonic()
            self._cycled_since_activity = False

    def attach(self, hook_manager):
        pairs = [("post_llm", self.poke), ("post_tool", self.poke), ("on_start", self.poke)]
        for event, handler in pairs:
            hook_manager.register(event, handler)
        self._registered = [(hook_manager, e, h) for e, h in pairs]

    def detach(self):
        for hook_manager, event, handler in self._registered:
            hook_manager.unregister(event, handler)
        self._registered = []

    # ── Thread lifecycle ──────────────────────────────────────
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hermes-subconscious")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self):
        while not self._stop.wait(self.poll_interval):
            try:
                if self._should_cycle():
                    self.run_cycle()
            except Exception as exc:
                self._log(f"cycle error: {exc}")

    def _should_cycle(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            idle_for = time.monotonic() - self._last_activity
            if idle_for < self.idle_seconds or self._cycled_since_activity:
                return False
            hour_ago = time.monotonic() - 3600
            self._cycle_times = [t for t in self._cycle_times if t > hour_ago]
            return len(self._cycle_times) < self.max_cycles_per_hour

    # ── The cycle ─────────────────────────────────────────────
    def run_cycle(self) -> dict:
        report: dict = {"at": datetime.now().isoformat(timespec="seconds")}
        try:
            if self.dreamer and self.session_loader:
                sessions = self.session_loader(5)
                report["dream"] = self.dreamer.dream(sessions, use_llm=self.use_llm)
        except Exception as exc:
            report["dream_error"] = str(exc)
        try:
            if self.distiller:
                report["distill"] = self.distiller.distill()
        except Exception as exc:
            report["distill_error"] = str(exc)
        with self._lock:
            self._cycled_since_activity = True
            self._cycle_times.append(time.monotonic())
            self._reports.append(report)
            self._reports = self._reports[-20:]
        self._log(f"cycle: {report}")
        return report

    def _log(self, line: str):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.root / "subconscious.log", "a") as fh:
                fh.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
        except OSError:
            pass

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "running": bool(self._thread and self._thread.is_alive()),
                "use_llm": self.use_llm,
                "idle_for_seconds": round(time.monotonic() - self._last_activity, 1),
                "idle_threshold_seconds": self.idle_seconds,
                "cycles_last_hour": len(self._cycle_times),
                "last_report": self._reports[-1] if self._reports else None,
            }
