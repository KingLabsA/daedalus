"""ModelOrchestra — agent-level mixture-of-experts across many LLM providers.

classify -> pick the right expert -> consult; or fan out a committee and synthesize.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

DEFAULT_PROFILES: Dict[str, List[str]] = {
    "code": ["deepseek", "anthropic", "openai", "mistral", "ollama", "hermes"],
    "reasoning": ["openai", "anthropic", "deepseek", "google", "xai"],
    "vision": ["openai", "google", "zhipu", "xai"],
    "cheap": ["groq", "cerebras", "ollama", "hermes", "google", "novita"],
    "creative": ["anthropic", "openai", "mistral", "moonshot"],
    "long_context": ["google", "anthropic", "openai", "moonshot"],
    "search": ["perplexity", "google", "openai"],
}

TASK_KEYWORDS: Dict[str, List[str]] = {
    "code": ["code", "function", "bug", "refactor", "implement", "compile", "test", "debug", "api", "class ", "def ", "regex"],
    "vision": ["image", "screenshot", "photo", "picture", "diagram", "ui mockup"],
    "search": ["latest", "news", "current", "today", "recent", "search the web"],
    "creative": ["story", "poem", "creative", "brainstorm", "name ideas", "slogan"],
    "long_context": ["entire file", "whole codebase", "long document", "summarize this document"],
    "reasoning": ["why", "prove", "reason", "plan", "architecture", "trade-off", "decide", "strategy"],
}


class ModelOrchestra:
    def __init__(
        self,
        call_fn: Callable[[str, str], str],
        available_fn: Callable[[], List[str]],
        profiles: Optional[Dict[str, List[str]]] = None,
    ):
        self.call_fn = call_fn
        self.available_fn = available_fn
        self.profiles = profiles or DEFAULT_PROFILES

    # ── Routing ───────────────────────────────────────────────
    def classify(self, prompt: str) -> str:
        lowered = prompt.lower()
        best, best_hits = "reasoning", 0
        for task_type, keywords in TASK_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in lowered)
            if hits > best_hits:
                best, best_hits = task_type, hits
        return best if best_hits else ("cheap" if len(prompt) < 80 else "reasoning")

    def pick(self, task_type: str) -> Optional[str]:
        available = self.available_fn() or []
        for provider in self.profiles.get(task_type, []):
            if provider in available:
                return provider
        return available[0] if available else None

    def consult(self, prompt: str, task_type: str = "") -> Dict:
        task_type = task_type or self.classify(prompt)
        provider = self.pick(task_type)
        if not provider:
            return {"task_type": task_type, "provider": None, "answer": "No providers available."}
        try:
            answer = str(self.call_fn(provider, prompt))
        except Exception as exc:
            answer = f"Expert call failed ({provider}): {exc}"
        return {"task_type": task_type, "provider": provider, "answer": answer}

    # ── Committee (MoE ensemble) ──────────────────────────────
    def _committee_members(self, task_type: str, n: int) -> List[str]:
        available = self.available_fn() or []
        members: List[str] = []
        for provider in self.profiles.get(task_type, []):
            if provider in available and provider not in members:
                members.append(provider)
            if len(members) >= n:
                return members
        for provider in available:  # top up with anything else available
            if provider not in members:
                members.append(provider)
            if len(members) >= n:
                break
        return members

    def committee(self, prompt: str, n: int = 3, task_type: str = "") -> Dict:
        task_type = task_type or self.classify(prompt)
        members = self._committee_members(task_type, max(2, n))
        if not members:
            return {"task_type": task_type, "experts": {}, "synthesis": "No providers available."}
        answers: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(members)) as pool:
            futures = {pool.submit(self.call_fn, m, prompt): m for m in members}
            for future in as_completed(futures):
                member = futures[future]
                try:
                    answers[member] = str(future.result())
                except Exception as exc:
                    answers[member] = f"(failed: {exc})"
        good = {m: a for m, a in answers.items() if not a.startswith("(failed:")}
        if not good:
            return {"task_type": task_type, "experts": answers, "synthesis": "All experts failed."}
        if len(good) == 1:
            return {"task_type": task_type, "experts": answers, "synthesis": next(iter(good.values()))}
        synth_provider = self.pick("reasoning") or next(iter(good))
        joined = "\n\n".join(f"--- Expert {m} ---\n{a[:2000]}" for m, a in good.items())
        try:
            synthesis = str(
                self.call_fn(
                    synth_provider,
                    "Multiple expert models answered the same question. Synthesize the single best "
                    f"answer, resolving disagreements with reasoning.\n\nQUESTION: {prompt}\n\n{joined}",
                )
            )
        except Exception:
            synthesis = max(good.values(), key=len)
        return {"task_type": task_type, "experts": answers, "synthesis": synthesis, "synthesizer": synth_provider}
