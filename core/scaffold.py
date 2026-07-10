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


def _tailwind(name: str) -> Dict[str, str]:
    """Vite + React + Tailwind — the modern UI baseline (shadcn-ready)."""
    files = _vite_react(name)
    pkg = json.loads(files["package.json"])
    pkg["devDependencies"].update({"tailwindcss": "^3.4.10", "postcss": "^8.4.41", "autoprefixer": "^10.4.20"})
    files["package.json"] = json.dumps(pkg, indent=2)
    files["tailwind.config.js"] = ("export default {\n  content: ['./index.html', './src/**/*.{js,jsx}'],\n"
                                    "  theme: { extend: {} },\n  plugins: [],\n}\n")
    files["postcss.config.js"] = "export default { plugins: { tailwindcss: {}, autoprefixer: {} } }\n"
    files["src/index.css"] = "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"
    files["src/main.jsx"] = ("import React from 'react'\nimport { createRoot } from 'react-dom/client'\n"
                             "import App from './App.jsx'\nimport './index.css'\n"
                             "createRoot(document.getElementById('root')).render(<App />)\n")
    files["src/App.jsx"] = (f"export default function App() {{\n  return (\n"
                            f"    <main className='min-h-screen grid place-items-center bg-slate-950 text-slate-100'>\n"
                            f"      <div className='text-center'>\n"
                            f"        <h1 className='text-4xl font-bold'>{name}</h1>\n"
                            f"        <p className='mt-2 text-slate-400'>Vite + React + Tailwind, scaffolded by Daedalus.</p>\n"
                            f"      </div>\n    </main>\n  )\n}}\n")
    files["README.md"] = f"# {name}\n\nVite + React + Tailwind (shadcn-ready).\n\n```bash\nnpm install\nnpm run dev   # http://localhost:5173\n```\n\nAdd components: `npx shadcn@latest init`\n"
    return files


def _supabase(name: str) -> Dict[str, str]:
    """Vite + React + Supabase client — instant auth/DB/storage backend."""
    files = _tailwind(name)
    pkg = json.loads(files["package.json"])
    pkg["dependencies"]["@supabase/supabase-js"] = "^2.45.0"
    files["package.json"] = json.dumps(pkg, indent=2)
    files["src/supabaseClient.js"] = ("import { createClient } from '@supabase/supabase-js'\n"
        "export const supabase = createClient(\n  import.meta.env.VITE_SUPABASE_URL,\n"
        "  import.meta.env.VITE_SUPABASE_ANON_KEY,\n)\n")
    files[".env.example"] = "VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co\nVITE_SUPABASE_ANON_KEY=YOUR_ANON_KEY\n"
    files["README.md"] = f"# {name} (Supabase)\n\n```bash\nnpm install\ncp .env.example .env   # fill from your Supabase project settings\nnpm run dev\n```\n"
    return files


def _astro(name: str) -> Dict[str, str]:
    slug = _slug(name)
    return {
        "package.json": json.dumps({"name": slug, "type": "module", "version": "0.1.0",
            "scripts": {"dev": "astro dev", "build": "astro build", "preview": "astro preview"},
            "dependencies": {"astro": "^4.15.0"}}, indent=2),
        "astro.config.mjs": "import { defineConfig } from 'astro/config'\nexport default defineConfig({})\n",
        "src/pages/index.astro": f"---\nconst title = '{name}'\n---\n<html><head><title>{{title}}</title></head>\n"
                                 f"<body style='font-family:system-ui;padding:40px'><h1>{{title}}</h1>\n"
                                 f"<p>Astro site scaffolded by Daedalus.</p></body></html>\n",
        "README.md": f"# {name} (Astro)\n\n```bash\nnpm install\nnpm run dev   # http://localhost:4321\n```\n",
    }


def _svelte(name: str) -> Dict[str, str]:
    slug = _slug(name)
    return {
        "package.json": json.dumps({"name": slug, "type": "module", "version": "0.1.0",
            "scripts": {"dev": "vite dev", "build": "vite build", "preview": "vite preview"},
            "devDependencies": {"@sveltejs/kit": "^2.5.0", "@sveltejs/adapter-auto": "^3.2.0",
                                "svelte": "^4.2.0", "vite": "^5.4.0"}}, indent=2),
        "svelte.config.js": "import adapter from '@sveltejs/adapter-auto'\nexport default { kit: { adapter: adapter() } }\n",
        "vite.config.js": "import { sveltekit } from '@sveltejs/kit/vite'\nexport default { plugins: [sveltekit()] }\n",
        "src/routes/+page.svelte": f"<h1>{name}</h1>\n<p>SvelteKit app scaffolded by Daedalus.</p>\n",
        "src/app.html": "<!doctype html><html><head>%sveltekit.head%</head><body>%sveltekit.body%</body></html>\n",
        "README.md": f"# {name} (SvelteKit)\n\n```bash\nnpm install\nnpm run dev   # http://localhost:5173\n```\n",
    }


def _mcp_server(name: str) -> Dict[str, str]:
    """Agent-authored MCP server — Daedalus extends its own toolset. Real stdio
    JSON-RPC server matching the McpClient protocol; register in .hermes/mcp.json."""
    slug = _slug(name)
    return {
        "server.py": f'''#!/usr/bin/env python3
"""{name} — an MCP (Model Context Protocol) server. Add tools in TOOLS below;
register it in .hermes/mcp.json and Daedalus can call them.
"""
import json
import sys

TOOLS = [
    {{"name": "ping", "description": "Health check", "inputSchema": {{"type": "object", "properties": {{}}}}}},
    {{"name": "echo", "description": "Echo a message",
      "inputSchema": {{"type": "object", "properties": {{"text": {{"type": "string"}}}}, "required": ["text"]}}}},
]


def handle(name, args):
    if name == "ping":
        return "pong"
    if name == "echo":
        return args.get("text", "")
    raise ValueError(f"unknown tool: {{name}}")


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        mid, method, params = req.get("id"), req.get("method"), req.get("params", {{}})
        if method == "initialize":
            send({{"jsonrpc": "2.0", "id": mid, "result": {{"protocolVersion": "2024-11-05",
                  "serverInfo": {{"name": "{slug}", "version": "0.1.0"}}, "capabilities": {{"tools": {{}}}}}}}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({{"jsonrpc": "2.0", "id": mid, "result": {{"tools": TOOLS}}}})
        elif method == "tools/call":
            try:
                out = handle(params.get("name"), params.get("arguments", {{}}))
                send({{"jsonrpc": "2.0", "id": mid, "result": {{"content": [{{"type": "text", "text": str(out)}}]}}}})
            except Exception as exc:
                send({{"jsonrpc": "2.0", "id": mid, "result": {{"content": [{{"type": "text", "text": str(exc)}}], "isError": True}}}})
        elif mid is not None:
            send({{"jsonrpc": "2.0", "id": mid, "result": {{}}}})


if __name__ == "__main__":
    main()
''',
        ".hermes/mcp.json": json.dumps({"servers": {slug: {"command": "python3", "args": ["server.py"], "env": {}}}}, indent=2),
        "README.md": f"# {name} — MCP server\n\nAdd tools in `TOOLS` + `handle()` in server.py.\n\n"
                     f"```bash\n# register (already written to .hermes/mcp.json), then in Daedalus:\n"
                     f"/mcp tools {slug}\n/mcp call {slug}/ping\n```\n",
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
    "tailwind": _tailwind,
    "shadcn": _tailwind,
    "supabase": _supabase,
    "astro": _astro,
    "svelte": _svelte,
    "sveltekit": _svelte,
    "mcp": _mcp_server,
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
    "tailwind": "cd {dir} && npm install && npm run dev",
    "shadcn": "cd {dir} && npm install && npm run dev",
    "supabase": "cd {dir} && npm install && cp .env.example .env && npm run dev",
    "astro": "cd {dir} && npm install && npm run dev",
    "svelte": "cd {dir} && npm install && npm run dev",
    "sveltekit": "cd {dir} && npm install && npm run dev",
    "mcp": "cd {dir} && python3 server.py   # then /mcp tools {slug} in Daedalus",
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
