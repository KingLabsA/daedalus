"""Token estimation and budget allocation for context injection."""
import os
from typing import Dict, List, Union

CHARS_PER_TOKEN = 4
MSG_OVERHEAD_TOKENS = 4


def estimate_tokens(payload: Union[str, List[Dict]]) -> int:
    """Rough token estimate: chars/4. Accepts a string or a message list."""
    if isinstance(payload, str):
        return max(1, len(payload) // CHARS_PER_TOKEN)
    total = 0
    for msg in payload:
        content = msg.get("content") or ""
        total += len(str(content)) // CHARS_PER_TOKEN + MSG_OVERHEAD_TOKENS
    return total


class TokenBudgeter:
    def __init__(self, max_context_tokens: int = 0, output_reserve: int = 4096, pressure: float = 0.75):
        if not max_context_tokens:
            max_context_tokens = int(os.getenv("HERMES_MAX_CONTEXT_TOKENS", "32000"))
        self.max_context_tokens = max_context_tokens
        self.output_reserve = min(output_reserve, max_context_tokens // 2)
        self.pressure = pressure

    @property
    def budget(self) -> int:
        return self.max_context_tokens - self.output_reserve

    def over_budget(self, messages: List[Dict]) -> bool:
        return estimate_tokens(messages) > self.budget * self.pressure

    def allocate(self, weights: Dict[str, float], total: int) -> Dict[str, int]:
        """Split `total` tokens across sections proportionally to weights."""
        wsum = sum(weights.values()) or 1.0
        return {name: int(total * w / wsum) for name, w in weights.items()}

    @staticmethod
    def clip(text: str, tokens: int) -> str:
        limit = tokens * CHARS_PER_TOKEN
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 12)] + "\n[...clipped]"
