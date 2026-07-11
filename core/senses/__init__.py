"""Hermes Senses — agent-level mixture-of-experts routing and multimodal I/O.

Standalone package: imports nothing from agent_ultimate. All provider calls injected.
"""

from .orchestra import ModelOrchestra
from .vision import Vision
from .voice import VoiceIO

__all__ = ["ModelOrchestra", "Vision", "VoiceIO"]
