"""CostAwareRouter — send easy work to cheap models, hard work to strong ones,
with thresholds informed by learned calibration rather than vibes.
"""

import re
from collections.abc import Callable

# cost tier per provider: 0=local/free, 1=cheap, 2=mid, 3=strong, 4=premium
PROVIDER_TIERS: dict[str, int] = {
    "ollama": 0,
    "hermes": 0,
    "freellmapi": 0,
    "groq": 1,
    "cerebras": 1,
    "novita": 1,
    "huggingface": 1,
    "together": 1,
    "deepseek": 2,
    "google": 2,
    "mistral": 2,
    "fireworks": 2,
    "moonshot": 2,
    "zhipu": 2,
    "cohere": 2,
    "perplexity": 2,
    "openai": 3,
    "xai": 3,
    "openrouter": 3,
    "azure": 3,
    "bedrock": 3,
    "replicate": 2,
    "anthropic": 4,
    "fable": 4,
    "opencode": 4,
}

HARD_MARKERS = [
    "architecture",
    "refactor",
    "concurrency",
    "race condition",
    "security",
    "prove",
    "design",
    "trade-off",
    "migrate",
    "distributed",
    "deadlock",
    "optimize",
    "debug this",
    "why does",
    "root cause",
    "plan",
]
EASY_MARKERS = ["rename", "typo", "format", "list files", "what time", "convert", "regex for", "one-liner"]


class CostAwareRouter:
    def __init__(
        self,
        available_fn: Callable[[], list[str]],
        tracker=None,
        tiers: dict[str, int] | None = None,
        threshold: float = 0.45,
    ):
        self.available_fn = available_fn
        self.tracker = tracker  # CalibrationTracker or None
        self.tiers = tiers or PROVIDER_TIERS
        self.threshold = threshold

    # ── Difficulty heuristic ──────────────────────────────────
    def difficulty(self, prompt: str) -> float:
        lowered = prompt.lower()
        score = 0.0
        score += min(0.35, len(prompt) / 4000)  # long prompts trend harder
        score += 0.12 * sum(1 for m in HARD_MARKERS if m in lowered)
        score -= 0.15 * sum(1 for m in EASY_MARKERS if m in lowered)
        if re.search(r"```|def |class |import ", prompt):
            score += 0.1
        if lowered.count("?") + lowered.count(" and ") >= 3:  # multi-part asks
            score += 0.15
        return max(0.0, min(1.0, score))

    # ── Routing ───────────────────────────────────────────────
    def _by_tier(self, ascending: bool) -> list[str]:
        available = [p for p in self.available_fn() if p in self.tiers]
        return sorted(available, key=lambda p: (self.tiers[p], p), reverse=not ascending)

    def route(self, prompt: str) -> dict:
        difficulty = self.difficulty(prompt)
        cheap_first = self._by_tier(ascending=True)
        strong_first = self._by_tier(ascending=False)
        if not cheap_first:
            return {"provider": None, "tier": None, "difficulty": difficulty, "reason": "no providers available"}
        threshold = self.threshold
        cheap = cheap_first[0]
        cheap_tier = self.tiers[cheap]
        # learned adjustment: if cheap-tier work has been failing, lower the bar for escalation
        if self.tracker:
            rate = self.tracker.success_rate(f"route_tier_{cheap_tier}")
            if rate is not None:
                threshold = threshold * (0.5 + rate)  # rate 0.9 -> ~1.4x threshold; rate 0.3 -> 0.8x
        if difficulty <= threshold:
            return {
                "provider": cheap,
                "tier": cheap_tier,
                "difficulty": round(difficulty, 2),
                "reason": f"difficulty {difficulty:.2f} <= threshold {threshold:.2f} -> cheapest live provider",
            }
        strong = strong_first[0]
        return {
            "provider": strong,
            "tier": self.tiers[strong],
            "difficulty": round(difficulty, 2),
            "reason": f"difficulty {difficulty:.2f} > threshold {threshold:.2f} -> strongest live provider",
        }

    def record_outcome(self, tier: int, success: bool, confidence: float = 0.7):
        if self.tracker is not None and tier is not None:
            self.tracker.record(f"route_tier_{tier}", confidence, success)
