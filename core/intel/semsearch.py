"""TF-IDF semantic-lite search over code chunks. Pure stdlib, offline, lazy-built."""
import math
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

from .codeintel import SKIP_DIRS

DEFAULT_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".json", ".toml", ".yaml", ".yml", ".rs", ".go", ".css", ".html"}
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def tokenize(text: str) -> List[str]:
    tokens = []
    for word in _WORD_RE.findall(text):
        for part in _CAMEL_RE.sub(" ", word).replace("_", " ").split():
            if len(part) >= 2:
                tokens.append(part.lower())
    return tokens


class SemanticIndex:
    def __init__(self, root: str = ".", exts=None, chunk_lines: int = 40, max_files: int = 2000):
        self.root = Path(root)
        self.exts = set(exts) if exts else DEFAULT_EXTS
        self.chunk_lines = chunk_lines
        self.max_files = max_files
        self._chunks: List[Dict] = []   # {file, line, tf: {token: weight}, norm}
        self._df: Counter = Counter()
        self._built = False

    def build(self) -> Dict:
        self._chunks, self._df = [], Counter()
        files = 0
        for path in sorted(self.root.rglob("*")):
            if files >= self.max_files:
                break
            if not path.is_file() or path.suffix not in self.exts:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                lines = path.read_text(errors="replace").splitlines()
            except OSError:
                continue
            files += 1
            rel = str(path.relative_to(self.root))
            for start in range(0, len(lines), self.chunk_lines):
                text = "\n".join(lines[start : start + self.chunk_lines])
                counts = Counter(tokenize(text))
                if not counts:
                    continue
                self._chunks.append({"file": rel, "line": start + 1, "counts": counts, "preview": text[:200]})
                self._df.update(counts.keys())
        n = max(1, len(self._chunks))
        for chunk in self._chunks:
            tf = {}
            for token, count in chunk["counts"].items():
                idf = math.log(1 + n / self._df[token])
                tf[token] = (1 + math.log(count)) * idf
            norm = math.sqrt(sum(w * w for w in tf.values())) or 1.0
            chunk["tf"] = tf
            chunk["norm"] = norm
            del chunk["counts"]
        self._built = True
        return {"files": files, "chunks": len(self._chunks), "vocab": len(self._df)}

    def search(self, query: str, k: int = 8) -> List[Dict]:
        if not self._built:
            self.build()
        q_counts = Counter(tokenize(query))
        if not q_counts or not self._chunks:
            return []
        n = len(self._chunks)
        q_vec = {
            token: (1 + math.log(count)) * math.log(1 + n / max(1, self._df.get(token, 0) or n))
            for token, count in q_counts.items()
        }
        q_norm = math.sqrt(sum(w * w for w in q_vec.values())) or 1.0
        scored = []
        for chunk in self._chunks:
            dot = sum(weight * chunk["tf"].get(token, 0.0) for token, weight in q_vec.items())
            if dot > 0:
                scored.append((dot / (q_norm * chunk["norm"]), chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"file": c["file"], "line": c["line"], "score": round(score, 4), "preview": c["preview"]}
            for score, c in scored[:k]
        ]
