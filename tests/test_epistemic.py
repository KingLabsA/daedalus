"""Tests for core.epistemic — calibration tracker, cost-aware router, max mode."""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.epistemic import CalibrationTracker, CostAwareRouter, MaxMode


def test_epistemic_no_agent_ultimate_dependency():
    import core.epistemic as ep

    for mod_file in Path(ep.__file__).parent.glob("*.py"):
        assert not re.search(r"^\s*(?:from|import)\s+agent_ultimate", mod_file.read_text(), re.M), mod_file.name


@pytest.fixture
def tracker(tmp_path):
    return CalibrationTracker(str(tmp_path / "test.db"))


# ── CalibrationTracker ───────────────────────────────────────


def test_calibrated_falls_back_with_thin_data(tracker):
    assert tracker.calibrated(0.9) == 0.9  # no history yet
    tracker.record("x", 0.9, True)
    assert tracker.calibrated(0.9) == 0.9  # still < MIN_SAMPLES


def test_calibrated_learns_overconfidence(tracker):
    # model says 0.9 but only succeeds 20% of the time
    for i in range(10):
        tracker.record("goal_judge", 0.92, success=(i < 2))
    adjusted = tracker.calibrated(0.9)
    assert adjusted < 0.4  # learned: "0.9" here really means ~0.25
    # kind filter isolates histories
    assert tracker.calibrated(0.9, kind="other_kind") == 0.9


def test_calibrated_laplace_smoothing(tracker):
    for _ in range(6):
        tracker.record("k", 0.55, True)
    assert tracker.calibrated(0.55) == pytest.approx(7 / 8)  # (6+1)/(6+2)


def test_success_rate_and_report(tracker):
    assert tracker.success_rate("tool_run") is None
    for i in range(8):
        tracker.record("tool_run", 0.8, success=(i % 2 == 0))
    rate = tracker.success_rate("tool_run")
    assert rate == pytest.approx(5 / 10)  # (4+1)/(8+2)
    report = tracker.report()
    assert report["total_events"] == 8
    assert report["by_kind"]["tool_run"]["n"] == 8
    assert report["buckets"][0]["actual"] == 0.5


def test_confidence_clamped(tracker):
    tracker.record("k", 7.5, True)  # out-of-range input
    report = tracker.report()
    assert report["buckets"][0]["range"].endswith("1.0")


# ── CostAwareRouter ──────────────────────────────────────────


def _router(available, tracker=None, threshold=0.45):
    return CostAwareRouter(available_fn=lambda: available, tracker=tracker, threshold=threshold)


def test_difficulty_ordering():
    router = _router(["openai"])
    easy = router.difficulty("fix a typo in README")
    hard = router.difficulty("design the architecture for a distributed system, analyze trade-offs, debug this race condition and plan the migration")
    assert easy < hard
    assert 0.0 <= easy <= 1.0 and 0.0 <= hard <= 1.0


def test_route_easy_to_cheap_hard_to_strong():
    router = _router(["fable", "groq", "deepseek"])
    easy = router.route("fix a typo")
    assert easy["provider"] == "groq" and easy["tier"] == 1
    hard = router.route("design the architecture for a distributed migration, analyze trade-offs and root cause the deadlock")
    assert hard["provider"] == "fable" and hard["tier"] == 4


def test_route_local_is_free_tier():
    router = _router(["ollama", "anthropic"])
    assert router.route("fix a typo")["provider"] == "ollama"


def test_route_no_providers():
    assert _router([]).route("x")["provider"] is None


def test_route_learned_escalation(tmp_path):
    tracker = CalibrationTracker(str(tmp_path / "t.db"))
    # cheap tier (groq=1) failing consistently -> threshold shrinks -> escalate
    for _ in range(10):
        tracker.record("route_tier_1", 0.7, success=False)
    borderline = "explain why this function is slow"  # mid difficulty
    without = _router(["groq", "fable"]).route(borderline)
    with_history = _router(["groq", "fable"], tracker=tracker).route(borderline)
    # with a failing cheap tier, routing should be at least as aggressive about escalating
    if without["provider"] == "groq":
        assert with_history["difficulty"] == without["difficulty"]
        # threshold shrank: 0.45 * (0.5 + ~0.09) ≈ 0.27
        assert "threshold 0.2" in with_history["reason"] or with_history["provider"] == "fable"


def test_record_outcome_roundtrip(tmp_path):
    tracker = CalibrationTracker(str(tmp_path / "t.db"))
    router = _router(["groq"], tracker=tracker)
    for _ in range(6):
        router.record_outcome(1, success=True)
    assert tracker.success_rate("route_tier_1") == pytest.approx(7 / 8)


# ── MaxMode ──────────────────────────────────────────────────


def _candidates(answers):
    return lambda prompt, n: dict(list(answers.items())[:n])


def test_max_mode_picks_judged_winner(tmp_path):
    tracker = CalibrationTracker(str(tmp_path / "t.db"))
    judge = lambda p: '{"scores": {"a": 3, "b": 9, "c": 5}, "confidence": 0.8}'
    mm = MaxMode(_candidates({"a": "meh", "b": "great answer", "c": "ok"}), judge_fn=judge, tracker=tracker)
    result = mm.run("question", n=3)
    assert result["winner"] == "b" and result["answer"] == "great answer"
    assert result["scores"]["b"] == 9.0
    assert tracker.report()["by_kind"]["max_mode"]["n"] == 1


def test_max_mode_garbage_judge_fails_open():
    mm = MaxMode(_candidates({"a": "short", "b": "much longer thorough answer"}), judge_fn=lambda p: "nonsense")
    result = mm.run("q")
    assert result["winner"] == "b"  # longest wins fail-open
    assert result["scores"] == {}


def test_max_mode_single_candidate_skips_judging():
    calls = []
    mm = MaxMode(_candidates({"only": "answer"}), judge_fn=lambda p: calls.append(p) or "{}")
    result = mm.run("q")
    assert result["winner"] == "only" and calls == []


def test_max_mode_filters_failures_and_empty():
    mm = MaxMode(_candidates({"a": "(failed: down)", "b": ""}), judge_fn=None)
    assert mm.run("q")["winner"] is None

    def boom(prompt, n):
        raise RuntimeError("pool exploded")

    assert "failed" in MaxMode(boom).run("q")["answer"]
