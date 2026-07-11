"""Hermes Intel — code intelligence, semantic search, and the causal world model.

Standalone package: imports nothing from agent_ultimate. Stdlib only.
"""

from .codeintel import CodeIntel
from .embeddings import EmbeddingIndex, HybridSearch
from .lsp import LspClient
from .semsearch import SemanticIndex
from .sentinel import WorldModelSentinel
from .worldmodel import CausalWorldModel

__all__ = ["CodeIntel", "SemanticIndex", "CausalWorldModel", "WorldModelSentinel", "LspClient", "EmbeddingIndex", "HybridSearch"]
