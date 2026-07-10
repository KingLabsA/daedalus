"""Project scaffolding — text-to-app. Generates runnable full-stack project
skeletons the agent can then flesh out. Standalone, stdlib-only, deterministic
(no network, no LLM): each template writes real files that build/run as-is.
"""
import json
import re
from pathlib import Path
from typing import Callable, Dict, List


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "app").lower()).strip("-")
    return s or "app"


# ── templates: each returns {relative_path: file_contents} ────────────────

def _vite_react(name: str) -> Dict[str, str]:
    slug = _slug(name)
    return {
        "package.json": json.dumps({
            "name": slug, "private": True, "version": "0.1.0", "type": "module",
            "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
            "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
            "devDependencies": {"@vitejs/plugin-react": "^4.3.1", "vite": "^5.4.0"},
        }, indent=2),
        "vite.config.js": "import { defineConfig } from 'vite'\nimport react from '@vitejs/plugin-react'\nexport default defineConfig({ plugins: [react()] })\n",
        "index.html": f'<!doctype html><html><head><meta charset="utf-8"><title>{name}</title></head>'
                      '<body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body></html>\n',
        "src/main.jsx": "import React from 'react'\nimport { createRoot } from 'react-dom/client'\nimport App from './App.jsx'\ncreateRoot(document.getElementById('root')).render(<App />)\n",
        "src/App.jsx": f"export default function App() {{\n  return <main style={{{{fontFamily:'system-ui',padding:40}}}}><h1>{name}</h1><p>Scaffolded by Daedalus. Edit src/App.jsx.</p></main>\n}}\n",
        "README.md": f"# {name}\n\n```bash\nnpm install\nnpm run dev   # http://localhost:5173\n```\n",
    }


def _fastapi(name: str) -> Dict[str, str]:
    return {
        "requirements.txt": "fastapi>=0.110\nuvicorn[standard]>=0.29\n",
        "main.py": f'''from fastapi import FastAPI

app = FastAPI(title="{name}")


@app.get("/")
def root():
    return {{"app": "{name}", "status": "ok"}}


@app.get("/api/health")
def health():
    return {{"healthy": True}}
''',
        "README.md": f"# {name} (FastAPI)\n\n```bash\npip install -r requirements.txt\nuvicorn main:app --reload   # http://127.0.0.1:8000\n```\n",
        "Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install -r requirements.txt\nCOPY . .\nCMD [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]\n",
    }


def _fullstack(name: str) -> Dict[str, str]:
    files = {f"frontend/{k}": v for k, v in _vite_react(name).items()}
    files.update({f"backend/{k}": v for k, v in _fastapi(name).items()})
    files["docker-compose.yml"] = (
        "services:\n"
        "  backend:\n    build: ./backend\n    ports: ['8000:8000']\n"
        "  frontend:\n    image: node:20-slim\n    working_dir: /app\n"
        "    volumes: ['./frontend:/app']\n    command: sh -c 'npm install && npm run dev -- --host'\n"
        "    ports: ['5173:5173']\n"
    )
    files["README.md"] = f"# {name} — full stack\n\nFrontend: Vite+React (5173) · Backend: FastAPI (8000)\n\n```bash\ndocker compose up\n```\n"
    return files


def _cli(name: str) -> Dict[str, str]:
    slug = _slug(name)
    return {
        "pyproject.toml": f'[project]\nname = "{slug}"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n\n[project.scripts]\n{slug} = "{slug.replace("-", "_")}:main"\n',
        f"{slug.replace('-', '_')}.py": f'''import argparse


def main():
    ap = argparse.ArgumentParser(prog="{slug}", description="{name}")
    ap.add_argument("name", nargs="?", default="world")
    args = ap.parse_args()
    print(f"Hello, {{args.name}} — from {name}")


if __name__ == "__main__":
    main()
''',
        "README.md": f"# {name} (CLI)\n\n```bash\npip install -e .\n{slug} yourname\n```\n",
    }


def _expo(name: str) -> Dict[str, str]:
    slug = _slug(name)
    return {
        "package.json": json.dumps({
            "name": slug, "version": "0.1.0",
            "main": "node_modules/expo/AppEntry.js",
            "scripts": {"start": "expo start", "android": "expo start --android", "ios": "expo start --ios"},
            "dependencies": {"expo": "~51.0.0", "react": "18.2.0", "react-native": "0.74.0"},
        }, indent=2),
        "App.js": f"import {{ Text, View }} from 'react-native';\nexport default function App() {{\n  return (<View style={{{{flex:1,alignItems:'center',justifyContent:'center'}}}}><Text>{name}</Text></View>);\n}}\n",
        "app.json": json.dumps({"expo": {"name": name, "slug": slug, "version": "0.1.0"}}, indent=2),
        "README.md": f"# {name} (Expo · iOS/Android)\n\n```bash\nnpm install\nnpx expo start   # scan QR with Expo Go, or press i / a\n```\n",
    }


TEMPLATES: Dict[str, Callable[[str], Dict[str, str]]] = {
    "web": _vite_react,
    "react": _vite_react,
    "api": _fastapi,
    "saas": _fullstack,
    "fullstack": _fullstack,
    "cli": _cli,
    "mobile": _expo,
    "ios": _expo,
    "android": _expo,
    "expo": _expo,
}

RUN_HINT = {
    "web": "cd {dir} && npm install && npm run dev",
    "react": "cd {dir} && npm install && npm run dev",
    "api": "cd {dir} && pip install -r requirements.txt && uvicorn main:app --reload",
    "saas": "cd {dir} && docker compose up",
    "fullstack": "cd {dir} && docker compose up",
    "cli": "cd {dir} && pip install -e . && {slug} --help",
    "mobile": "cd {dir} && npm install && npx expo start",
    "ios": "cd {dir} && npm install && npx expo start --ios",
    "android": "cd {dir} && npm install && npx expo start --android",
    "expo": "cd {dir} && npm install && npx expo start",
}


def kinds() -> List[str]:
    return sorted(TEMPLATES)


def scaffold(kind: str, name: str, out_dir: str = "") -> Dict:
    """Write a project skeleton. Returns {ok, kind, dir, files, run}."""
    kind = (kind or "web").lower().strip()
    builder = TEMPLATES.get(kind)
    if not builder:
        return {"ok": False, "error": f"Unknown kind '{kind}'. Options: {', '.join(kinds())}"}
    target = Path(out_dir) if out_dir else Path(_slug(name))
    try:
        written = []
        for rel, content in builder(name).items():
            fp = target / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
            written.append(str(fp))
        run = RUN_HINT.get(kind, "").format(dir=target, slug=_slug(name))
        return {"ok": True, "kind": kind, "dir": str(target), "files": written, "run": run}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
