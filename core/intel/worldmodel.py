"""Causal World Model — predicts the blast radius of an edit before it happens.

Built from git co-change history (files that historically change together) plus
the Python import graph (fan-in). No dependencies beyond git itself.
"""

import ast
import subprocess
from collections import Counter
from itertools import combinations
from pathlib import Path

from .codeintel import _iter_source_files

MEGA_COMMIT_CAP = 30  # commits touching more files than this are skipped (renames/vendoring skew)
RISK_WARN_THRESHOLD = 0.3


class CausalWorldModel:
    def __init__(self, repo_path: str = ".", max_commits: int = 500):
        self.repo = Path(repo_path)
        self.max_commits = max_commits
        self.co_change: Counter = Counter()  # {(a, b) sorted: count}
        self.file_freq: Counter = Counter()  # {file: commits touching it}
        self.fan_in: dict[str, list[str]] = {}  # {file: [files importing it]}
        self.built = False

    # ── Building ──────────────────────────────────────────────
    def build(self) -> dict:
        self._mine_git()
        self._mine_imports()
        self.built = True
        return {
            "files_seen": len(self.file_freq),
            "co_change_pairs": len(self.co_change),
            "files_with_importers": len(self.fan_in),
        }

    def _mine_git(self):
        try:
            proc = subprocess.run(
                ["git", "log", "--name-only", "--pretty=format:@@COMMIT@@", "-n", str(self.max_commits)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.repo,
            )
            raw = proc.stdout
        except (subprocess.TimeoutExpired, OSError):
            return
        commit_files: list[str] = []
        commits: list[list[str]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line == "@@COMMIT@@":
                if commit_files:
                    commits.append(commit_files)
                commit_files = []
            else:
                commit_files.append(line)
        if commit_files:
            commits.append(commit_files)
        for files in commits:
            files = sorted(set(files))
            if not (2 <= len(files) <= MEGA_COMMIT_CAP):
                for f in files:
                    self.file_freq[f] += 1
                continue
            for f in files:
                self.file_freq[f] += 1
            for a, b in combinations(files, 2):
                self.co_change[(a, b)] += 1

    def _mine_imports(self):
        stem_to_files: dict[str, list[str]] = {}
        py_files = list(_iter_source_files(self.repo, {".py"}, max_files=2000))
        for path in py_files:
            stem_to_files.setdefault(path.stem, []).append(str(path.relative_to(self.repo)))
        importers: dict[str, list[str]] = {}
        for path in py_files:
            rel = str(path.relative_to(self.repo))
            try:
                tree = ast.parse(path.read_text(errors="replace"))
            except (SyntaxError, OSError):
                continue
            modules = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.update(alias.name.split(".")[-1] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module.split(".")[-1])
            for mod in modules:
                for target in stem_to_files.get(mod, []):
                    if target != rel:
                        importers.setdefault(target, []).append(rel)
        self.fan_in = importers

    # ── Queries ───────────────────────────────────────────────
    def co_changed(self, filepath: str, k: int = 8) -> list[tuple[str, float]]:
        if not self.built:
            self.build()
        freq = self.file_freq.get(filepath, 0)
        if not freq:
            return []
        related = []
        for (a, b), count in self.co_change.items():
            other = b if a == filepath else a if b == filepath else None
            if other:
                related.append((other, round(count / freq, 3)))
        related.sort(key=lambda pair: pair[1], reverse=True)
        return related[:k]

    def blast_radius(self, filepath: str) -> dict:
        if not self.built:
            self.build()
        co = [(f, s) for f, s in self.co_changed(filepath) if s >= 0.25]
        importers = self.fan_in.get(filepath, [])
        risk = min(1.0, 0.08 * len(importers) + 0.06 * len(co))
        reasons = []
        if importers:
            reasons.append(f"{len(importers)} file(s) import it")
        if co:
            reasons.append(f"{len(co)} file(s) historically change with it")
        return {
            "file": filepath,
            "risk": round(risk, 2),
            "importers": importers[:10],
            "co_changes": co,
            "reasons": reasons,
        }

    def render_warning(self, filepath: str) -> str:
        radius = self.blast_radius(filepath)
        if radius["risk"] < RISK_WARN_THRESHOLD:
            return ""
        lines = [f"[WORLD MODEL] Editing {filepath} is HIGH BLAST RADIUS (risk {radius['risk']})."]
        if radius["importers"]:
            lines.append("  imported by: " + ", ".join(radius["importers"][:6]))
        if radius["co_changes"]:
            lines.append("  historically changes with: " + ", ".join(f"{f} ({int(s * 100)}%)" for f, s in radius["co_changes"][:6]))
        lines.append("  -> after editing, verify these files still work (tests/diagnostics).")
        return "\n".join(lines)
