"""Hermes Cognition — self-evolution: dream, distill, judged goals, sleep-time compute.

Standalone package: imports nothing from agent_ultimate. Configure via constructor.
"""
from .events import EventLog
from .dream import Dreamer
from .distill import Distiller
from .judge import GoalJudge
from .subconscious import Subconscious

__all__ = ["EventLog", "Dreamer", "Distiller", "GoalJudge", "Subconscious"]
