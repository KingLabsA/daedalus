"""MaxMode — judged best-of-N. Burn compute deliberately: N candidates from
distinct models, an independent judge scores them, the winner is returned and
the judge's implied confidence feeds the calibration tracker.
"""

import json
import re
from collections.abc import Callable

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


class MaxMode:
    def __init__(
        self,
        candidates_fn: Callable[[str, int], dict[str, str]],
        judge_fn: Callable[[str], str] | None = None,
        tracker=None,
    ):
        """candidates_fn(prompt, n) -> {provider: answer}; judge_fn(prompt) -> str (LLM)."""
        self.candidates_fn = candidates_fn
        self.judge_fn = judge_fn
        self.tracker = tracker

    def _score(self, prompt: str, candidates: dict[str, str]) -> dict[str, float]:
        if not self.judge_fn:
            return {}
        listing = "\n\n".join(f"### CANDIDATE {name}\n{answer[:2500]}" for name, answer in candidates.items())
        judge_prompt = (
            "You are a strict judge. Score each candidate answer 0-10 for correctness, "
            "completeness, and usefulness for the question. Reply with JSON only: "
            '{"scores": {"<candidate-name>": <number>, ...}, "confidence": 0.0-1.0}\n\n'
            f"QUESTION: {prompt}\n\n{listing}"
        )
        try:
            raw = str(self.judge_fn(judge_prompt))
            match = _JSON_OBJ_RE.search(raw)
            data = json.loads(match.group(0)) if match else {}
            scores = {str(k): float(v) for k, v in (data.get("scores") or {}).items() if str(k) in candidates}
            if self.tracker and scores:
                confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
                spread = (max(scores.values()) - min(scores.values())) / 10 if len(scores) > 1 else 0
                # a decisive judge on a wide spread is a confident prediction
                self.tracker.record("max_mode", max(confidence, spread), True)
            return scores
        except Exception:
            return {}

    def run(self, prompt: str, n: int = 3) -> dict:
        try:
            candidates = self.candidates_fn(prompt, n) or {}
        except Exception as exc:
            return {"winner": None, "answer": f"Candidate generation failed: {exc}", "candidates": {}, "scores": {}}
        candidates = {k: v for k, v in candidates.items() if v and not str(v).startswith("(failed:")}
        if not candidates:
            return {"winner": None, "answer": "No candidates produced.", "candidates": {}, "scores": {}}
        if len(candidates) == 1:
            name, answer = next(iter(candidates.items()))
            return {"winner": name, "answer": answer, "candidates": candidates, "scores": {}}
        scores = self._score(prompt, candidates)
        if scores:
            winner = max(scores, key=scores.get)
        else:
            winner = max(candidates, key=lambda k: len(candidates[k]))  # fail-open: most thorough
        return {
            "winner": winner,
            "answer": candidates[winner],
            "candidates": {k: v[:500] for k, v in candidates.items()},
            "scores": scores,
        }
