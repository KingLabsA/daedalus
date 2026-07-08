"""Hermes Intel — code intelligence, semantic search, and the causal world model.

Standalone package: imports nothing from agent_ultimate. Stdlib only.
"""
from .codeintel import CodeIntel
from .semsearch import SemanticIndex
from .worldmodel import CausalWorldModel
from .sentinel import WorldModelSentinel
from .lsp import LspClient
from .embeddings import EmbeddingIndex, HybridSearch

__all__ = ["CodeIntel", "SemanticIndex", "CausalWorldModel", "WorldModelSentinel",
           "LspClient", "EmbeddingIndex", "HybridSearch"]
