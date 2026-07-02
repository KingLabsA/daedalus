"""DependencyScanner — scans the device Hermes is installed on for missing pieces.

Every Hermes capability degrades gracefully when a dependency is absent; the doctor
tells the user exactly what is missing, what it unlocks, and how to install it.
"""
import importlib.util
import os
import shutil
from typing import Callable, Dict, List, Optional

BINARIES = [
    # (binary, needed_for, install_hint)
    ("git", "checkpoints, causal world model, version control tools", "xcode-select --install  # or: brew install git"),
    ("docker", "sandboxed command execution", "brew install --cask docker"),
    ("node", "MCP servers (npx), desktop app build", "brew install node"),
    ("npm", "desktop app (React frontend), npx MCP servers", "brew install node"),
    ("ollama", "free local models (offline, private)", "brew install ollama"),
    ("ffmpeg", "video analysis (frame extraction), audio fallback", "brew install ffmpeg"),
    ("sox", "microphone recording for /listen voice input", "brew install sox"),
    ("pyright", "deep type-aware code diagnostics", "npm install -g pyright"),
    ("tsc", "TypeScript diagnostics", "npm install -g typescript"),
    ("afplay", "text-to-speech playback (macOS builtin)", "(macOS builtin)"),
    ("rg", "fast code search fallback", "brew install ripgrep"),
]

PY_PACKAGES = [
    ("openai", "all OpenAI-compatible providers (most of the 21)", "pip install openai"),
    ("anthropic", "Anthropic / Claude Fable 5 provider", "pip install anthropic"),
    ("websockets", "WebSocket server for desktop/web app", "pip install websockets"),
    ("dotenv", "loading .env provider keys", "pip install python-dotenv"),
    ("playwright", "advanced browser control", "pip install playwright && playwright install chromium"),
    ("pyautogui", "desktop control (mouse/keyboard)", "pip install pyautogui"),
    ("pytest", "self-verification of generated code", "pip install pytest"),
]


class DependencyScanner:
    def __init__(
        self,
        which_fn: Callable[[str], Optional[str]] = shutil.which,
        provider_configs: Optional[Dict[str, dict]] = None,
        env: Optional[dict] = None,
    ):
        self.which_fn = which_fn
        self.provider_configs = provider_configs or {}
        self.env = env if env is not None else dict(os.environ)

    def scan(self) -> Dict:
        ok, missing = [], []
        for binary, needed_for, hint in BINARIES:
            if self.which_fn(binary):
                ok.append(binary)
            else:
                missing.append({"name": binary, "kind": "binary", "needed_for": needed_for, "install": hint})
        for package, needed_for, hint in PY_PACKAGES:
            if importlib.util.find_spec(package) is not None:
                ok.append(f"py:{package}")
            else:
                missing.append({"name": package, "kind": "python", "needed_for": needed_for, "install": hint})
        providers = {}
        for name, cfg in self.provider_configs.items():
            env_key = cfg.get("env", "")
            providers[name] = "ready" if (not env_key or self.env.get(env_key)) else f"missing {env_key}"
        live = [n for n, s in providers.items() if s == "ready"]
        disk_free_gb = None
        try:
            disk_free_gb = round(shutil.disk_usage(".").free / 1e9, 1)
        except OSError:
            pass
        return {
            "ok": ok,
            "missing": missing,
            "providers": providers,
            "providers_live": live,
            "disk_free_gb": disk_free_gb,
        }

    def fix_script(self, report: Optional[Dict] = None) -> str:
        report = report or self.scan()
        lines = ["# Hermes doctor — run these to unlock missing capabilities:"]
        for item in report["missing"]:
            if "builtin" in item["install"]:
                continue
            lines.append(f"{item['install']}    # {item['needed_for']}")
        if len(lines) == 1:
            return "# Nothing to fix — all dependencies present."
        return "\n".join(lines)

    def summary(self, report: Optional[Dict] = None) -> str:
        report = report or self.scan()
        parts = [
            f"Doctor: {len(report['ok'])} dependencies OK, {len(report['missing'])} missing, "
            f"{len(report['providers_live'])}/{len(report['providers']) or '?'} providers live"
        ]
        if report.get("disk_free_gb") is not None:
            parts.append(f"disk free: {report['disk_free_gb']} GB")
        for item in report["missing"][:8]:
            parts.append(f"  MISSING {item['name']}: {item['needed_for']} -> {item['install']}")
        return "\n".join(parts)
