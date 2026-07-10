"""Deploy layer — text-to-shipped. Detects a project's type, writes the real
provider config file, checks the required CLI, and returns the exact command
sequence to deploy. Standalone, stdlib-only. Does NOT hold cloud credentials —
it prepares everything and hands back the one authenticated command to run
(honest: real deploys require the user's own `login`).
"""
import json
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ── project detection ─────────────────────────────────────────────────────

def detect(project_dir: str = ".") -> str:
    p = Path(project_dir)
    def has(*names): return any((p / n).exists() for n in names)
    if has("app.json") and (p / "package.json").exists():
        try:
            if "expo" in json.loads((p / "package.json").read_text()).get("dependencies", {}):
                return "expo"
        except (OSError, ValueError):
            pass
    if (p / "frontend").is_dir() and (p / "backend").is_dir():
        return "fullstack"
    if has("next.config.js", "next.config.mjs", "next.config.ts"):
        return "next"
    if (p / "package.json").exists():
        try:
            pkg = json.loads((p / "package.json").read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            if "vite" in deps or "react-scripts" in deps:
                return "static"
        except (OSError, ValueError):
            pass
        return "node"
    if has("Dockerfile"):
        return "docker"
    if has("main.py", "requirements.txt", "pyproject.toml"):
        return "python"
    return "unknown"


# ── target catalog: kind -> {target -> spec} ──────────────────────────────
# spec: cli (binary), install (hint), config (filename), config_body(name)->str, steps[]

def _fly_toml(app: str, internal_port: int) -> str:
    return (f'app = "{app}"\nprimary_region = "iad"\n\n'
            f'[build]\n\n[http_service]\n  internal_port = {internal_port}\n'
            '  force_https = true\n  auto_stop_machines = true\n  auto_start_machines = true\n')

def _vercel_json(_: str) -> str:
    return json.dumps({"version": 2}, indent=2)

def _netlify_toml(_: str) -> str:
    return '[build]\n  command = "npm run build"\n  publish = "dist"\n'


def targets(kind: str, app: str) -> Dict[str, Dict]:
    app = app or "app"
    static = {
        "vercel": {"cli": "vercel", "install": "npm i -g vercel",
                   "config": "vercel.json", "body": _vercel_json,
                   "steps": ["vercel login", "vercel --prod"]},
        "netlify": {"cli": "netlify", "install": "npm i -g netlify-cli",
                    "config": "netlify.toml", "body": _netlify_toml,
                    "steps": ["netlify login", "npm run build", "netlify deploy --prod --dir dist"]},
    }
    server = {
        "fly": {"cli": "flyctl", "install": "brew install flyctl",
                "config": "fly.toml", "body": lambda a: _fly_toml(a, 8000),
                "steps": ["flyctl auth login", "flyctl launch --now --copy-config"]},
    }
    web_server = {
        "fly": {"cli": "flyctl", "install": "brew install flyctl",
                "config": "fly.toml", "body": lambda a: _fly_toml(a, 5173),
                "steps": ["flyctl auth login", "flyctl launch --now --copy-config"]},
    }
    catalog = {
        "static": static,
        "next": {**static, **web_server},
        "node": web_server,
        "python": server,
        "docker": server,
        "fullstack": {"fly": server["fly"], **{f"frontend-{k}": v for k, v in static.items()}},
        "expo": {
            "eas": {"cli": "eas", "install": "npm i -g eas-cli",
                    "config": "eas.json",
                    "body": lambda a: json.dumps({"cli": {"version": ">= 5.0.0"},
                        "build": {"production": {}}, "submit": {"production": {}}}, indent=2),
                    "steps": ["eas login", "eas build --platform all --profile production",
                              "eas submit --platform all"]},
        },
    }
    return catalog.get(kind, {})


def _needs_dockerfile(kind: str, p: Path) -> Optional[str]:
    if kind in ("python", "node") and not (p / "Dockerfile").exists():
        if (p / "main.py").exists():
            return ("FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\n"
                    "RUN pip install -r requirements.txt\nCOPY . .\n"
                    'CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]\n')
    return None


# ── public API ────────────────────────────────────────────────────────────

def plan(project_dir: str = ".", target: str = "", app: str = "",
         which: Callable[[str], Optional[str]] = shutil.which, write: bool = True) -> Dict:
    """Detect the project, write the provider config, and return the deploy plan."""
    p = Path(project_dir)
    if not p.exists():
        return {"ok": False, "error": f"No such directory: {project_dir}"}
    kind = detect(project_dir)
    app = app or p.resolve().name or "app"
    opts = targets(kind, app)
    if not opts:
        return {"ok": False, "kind": kind, "error": f"No deploy target known for project kind '{kind}'."}
    if not target:
        return {"ok": True, "kind": kind, "app": app, "targets": sorted(opts),
                "hint": f"Pick a target: {', '.join(sorted(opts))}"}
    spec = opts.get(target)
    if not spec:
        return {"ok": False, "kind": kind, "error": f"Unknown target '{target}'. Options: {', '.join(sorted(opts))}"}

    written = []
    if write:
        try:
            df = _needs_dockerfile(kind, p)
            if df:
                (p / "Dockerfile").write_text(df); written.append("Dockerfile")
            cfg = p / spec["config"]
            cfg.write_text(spec["body"](app)); written.append(spec["config"])
        except OSError as exc:
            return {"ok": False, "error": f"Could not write config: {exc}"}

    cli_present = bool(which(spec["cli"]))
    return {
        "ok": True, "kind": kind, "app": app, "target": target,
        "cli": spec["cli"], "cli_installed": cli_present,
        "install": None if cli_present else spec["install"],
        "config_written": written,
        "steps": [f"cd {project_dir}"] + spec["steps"],
        "note": ("Ready — run the steps (the login step opens your browser)."
                 if cli_present else f"Install the CLI first: {spec['install']}"),
    }
