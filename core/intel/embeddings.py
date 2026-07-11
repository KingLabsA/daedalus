"""EmbeddingIndex — semantic code search via a local embedding model (Ollama),
with per-file content-hash caching and TF-IDF fallback through HybridSearch.
"""

import hashlib
import json
import math
import os
from collections.abc import Callable
from pathlib import Path

from .codeintel import SKIP_DIRS
from .semsearch import DEFAULT_EXTS

MAX_FILES = 500
MAX_CHUNKS = 4000
CHUNK_LINES = 40


def default_embed_fn(texts: list[str]) -> list[list[float]] | None:
    """Embed via Ollama's API. Returns None when unavailable (triggers fallback)."""
    import requests

    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    model = os.getenv("HERMES_EMBED_MODEL", "nomic-embed-text")
    out = []
    try:
        for text in texts:
            r = requests.post(f"{host}/api/embeddings", json={"model": model, "prompt": text[:4000]}, timeout=30)
            if r.status_code != 200:
                return None
            out.append(r.json().get("embedding") or [])
        return out if all(out) else None
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class EmbeddingIndex:
    def __init__(self, root: str = ".", embed_fn: Callable = default_embed_fn, cache_path: str = ".hermes/embed_cache.json"):
        self.root = Path(root)
        self.embed_fn = embed_fn
        self.cache_path = Path(cache_path)
        self._chunks: list[dict] = []  # {file, line, text, vec}
        self._built = False

    def available(self) -> bool:
        probe = self.embed_fn(["ping"])
        return bool(probe)

    def _load_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_cache(self, cache: dict):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(cache))
        except OSError:
            pass

    def build(self) -> dict:
        cache = self._load_cache()
        new_cache: dict = {}
        self._chunks = []
        files = embedded = 0
        for path in sorted(self.root.rglob("*")):
            if files >= MAX_FILES or len(self._chunks) >= MAX_CHUNKS:
                break
            if not path.is_file() or path.suffix not in DEFAULT_EXTS:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.resolve() == self.cache_path.resolve():
                continue  # never index our own cache
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            files += 1
            rel = str(path.relative_to(self.root))
            digest = hashlib.sha256(text.encode()).hexdigest()[:16]
            cached = cache.get(rel)
            if cached and cached.get("hash") == digest:
                for c in cached["chunks"]:
                    self._chunks.append({"file": rel, **c})
                new_cache[rel] = cached
                continue
            lines = text.splitlines()
            file_chunks = []
            pending_texts, pending_meta = [], []
            for start in range(0, len(lines), CHUNK_LINES):
                chunk_text = "\n".join(lines[start : start + CHUNK_LINES])
                if chunk_text.strip():
                    pending_texts.append(chunk_text)
                    pending_meta.append(start + 1)
            vecs = self.embed_fn(pending_texts) if pending_texts else []
            if vecs is None:
                return {"error": "embedding model unavailable", "files": files, "chunks": len(self._chunks)}
            for line_no, chunk_text, vec in zip(pending_meta, pending_texts, vecs):
                entry = {"line": line_no, "text": chunk_text[:200], "vec": vec}
                file_chunks.append(entry)
                self._chunks.append({"file": rel, **entry})
                embedded += 1
            new_cache[rel] = {"hash": digest, "chunks": file_chunks}
        self._save_cache(new_cache)
        self._built = True
        return {"files": files, "chunks": len(self._chunks), "embedded_new": embedded}

    def search(self, query: str, k: int = 8) -> list[dict]:
        if not self._built:
            report = self.build()
            if report.get("error"):
                return []
        qvecs = self.embed_fn([query])
        if not qvecs:
            return []
        qvec = qvecs[0]
        scored = sorted(
            ((_cosine(qvec, c["vec"]), c) for c in self._chunks),
            key=lambda p: p[0],
            reverse=True,
        )
        return [{"file": c["file"], "line": c["line"], "score": round(s, 4), "preview": c["text"]} for s, c in scored[:k] if s > 0]


class HybridSearch:
    """Embeddings when the model answers; TF-IDF otherwise. Same result shape."""

    def __init__(self, embedding: EmbeddingIndex, tfidf):
        self.embedding = embedding
        self.tfidf = tfidf
        self._embed_ok: bool | None = None

    def mode(self) -> str:
        if self._embed_ok is None:
            self._embed_ok = self.embedding.available()
        return "embeddings" if self._embed_ok else "tfidf"

    def search(self, query: str, k: int = 8) -> list[dict]:
        if self.mode() == "embeddings":
            hits = self.embedding.search(query, k)
            if hits:
                return hits
            self._embed_ok = False  # model died mid-flight: fall back
        return self.tfidf.search(query, k)
