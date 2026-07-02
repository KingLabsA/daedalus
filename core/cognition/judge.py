"""GoalJudge — independent completion verdicts to prevent optimistic early stops."""
import json
import re
from typing import Callable, Dict, List, Optional

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


class GoalJudge:
    def __init__(self, llm_fn: Optional[Callable[[str], str]] = None, tail: int = 12):
        self.llm_fn = llm_fn
        self.tail = tail

    def verdict(self, goal: str, messages: List[Dict]) -> Dict:
        """Fail-open: with no judge model or an unparseable reply, complete=True so the
        agent can never be trapped in an infinite loop by its own judge."""
        if not self.llm_fn:
            return {"complete": True, "reason": "no judge model configured", "confidence": 0.0}
        transcript = "\n".join(
            f"{m.get('role')}: {str(m.get('content') or '')[:300]}" for m in messages[-self.tail :]
        )
        prompt = (
            "You are a strict, independent judge evaluating whether an autonomous coding agent "
            "has TRULY completed its goal. Agents often claim success prematurely — verify against "
            "evidence in the transcript (tool results, test output), not against the agent's claims.\n\n"
            f"GOAL: {goal}\n\nTRANSCRIPT (recent):\n{transcript}\n\n"
            'Reply with JSON only: {"complete": true|false, "reason": "<one sentence>", "confidence": 0.0-1.0}'
        )
        try:
            raw = str(self.llm_fn(prompt))
        except Exception as exc:
            return {"complete": True, "reason": f"judge unavailable: {exc}", "confidence": 0.0}
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> Dict:
        match = _JSON_OBJ_RE.search(raw)
        if match:
            try:
                data = json.loads(match.group(0))
                return {
                    "complete": bool(data.get("complete", True)),
                    "reason": str(data.get("reason", ""))[:400],
                    "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
                }
            except (ValueError, TypeError):
                pass
        lowered = raw.lower()
        if '"complete": false' in lowered or "not complete" in lowered or "incomplete" in lowered:
            return {"complete": False, "reason": raw.strip()[:400], "confidence": 0.3}
        return {"complete": True, "reason": "unparseable judge reply (fail-open)", "confidence": 0.0}
