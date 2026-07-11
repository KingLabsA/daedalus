"""Lightweight code intelligence: symbols, definitions, references, diagnostics.

Python via ast, JS/TS via regex — no LSP server processes required.
"""

import ast
import py_compile
import re
import shutil
import subprocess
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv", ".hermes", ".tox", "target"}
PY_EXTS = {".py"}
JS_EXTS = {".js", ".jsx", ".ts", ".tsx"}

_JS_DEF_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?"
    r"(?:(?:async\s+)?function\s+(?P<fn>\w+)|class\s+(?P<cls>\w+)|(?:const|let|var)\s+(?P<var>\w+)\s*=\s*(?:async\s*)?(?:\(|function))",
    re.M,
)


def _iter_source_files(root: Path, exts: set, max_files: int = 3000):
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= max_files:
            return
        if not path.is_file() or path.suffix not in exts:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        count += 1
        yield path


class CodeIntel:
    def __init__(self, root: str = "."):
        self.root = Path(root)

    # ── Symbols ───────────────────────────────────────────────
    def symbols(self, filepath: str) -> list[dict]:
        path = Path(filepath)
        if not path.exists():
            return []
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return []
        if path.suffix in PY_EXTS:
            return self._py_symbols(text)
        if path.suffix in JS_EXTS:
            return self._js_symbols(text)
        return []

    @staticmethod
    def _py_symbols(text: str) -> list[dict]:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        out = []

        def walk(node, prefix=""):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append({"name": prefix + child.name, "kind": "function", "line": child.lineno})
                    walk(child, prefix + child.name + ".")
                elif isinstance(child, ast.ClassDef):
                    out.append({"name": prefix + child.name, "kind": "class", "line": child.lineno})
                    walk(child, prefix + child.name + ".")

        walk(tree)
        return out

    @staticmethod
    def _js_symbols(text: str) -> list[dict]:
        out = []
        for match in _JS_DEF_RE.finditer(text):
            name = match.group("fn") or match.group("cls") or match.group("var")
            kind = "class" if match.group("cls") else "function"
            line = text[: match.start()].count("\n") + 1
            out.append({"name": name, "kind": kind, "line": line})
        return out

    # ── Definitions / references ──────────────────────────────
    def find_definition(self, name: str, max_results: int = 10) -> list[dict]:
        out = []
        base = name.split(".")[-1]
        for path in _iter_source_files(self.root, PY_EXTS | JS_EXTS):
            for sym in self.symbols(str(path)):
                if sym["name"] == name or sym["name"].split(".")[-1] == base:
                    out.append({"file": str(path.relative_to(self.root)), **sym})
                    if len(out) >= max_results:
                        return out
        return out

    def references(self, name: str, max_results: int = 50) -> list[dict]:
        word = re.compile(r"\b" + re.escape(name) + r"\b")
        out = []
        for path in _iter_source_files(self.root, PY_EXTS | JS_EXTS):
            try:
                for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    if word.search(line):
                        out.append({"file": str(path.relative_to(self.root)), "line": lineno, "text": line.strip()[:160]})
                        if len(out) >= max_results:
                            return out
            except OSError:
                continue
        return out

    # ── Diagnostics ───────────────────────────────────────────
    def diagnostics(self, path: str = ".") -> str:
        target = Path(path) if Path(path).is_absolute() else self.root / path
        if shutil.which("pyright"):
            try:
                proc = subprocess.run(["pyright", "--outputjson", str(target)], capture_output=True, text=True, timeout=120)
                return proc.stdout[:8000] or proc.stderr[:2000]
            except (subprocess.TimeoutExpired, OSError) as exc:
                return f"pyright failed: {exc}"
        # stdlib fallback: syntax-check every python file
        issues = []
        files = [target] if target.is_file() else list(_iter_source_files(target, PY_EXTS, max_files=200))
        for f in files:
            try:
                py_compile.compile(str(f), doraise=True)
            except py_compile.PyCompileError as exc:
                issues.append(str(exc.msg)[:300])
            except OSError:
                continue
        if not issues:
            return f"OK: {len(files)} python file(s) compile cleanly (pyright not installed; syntax check only)"
        return "\n".join(issues[:40])
