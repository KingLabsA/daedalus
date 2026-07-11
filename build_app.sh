#!/usr/bin/env bash
# Build the standalone Daedalus desktop app (macOS .app / Linux binary).
# Produces dist/Daedalus.app — no Tauri, no Rust, no browser dependency.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Ensuring the web UI is built and bundled..."
[ -f hermes_webui/index.html ] || ./scripts/sync_webui.sh

echo "==> Installing build deps (pyinstaller, pywebview)..."
python3 -m pip install --quiet pyinstaller pywebview

echo "==> Building with PyInstaller..."
python3 -m PyInstaller --noconfirm daedalus.spec

echo
echo "✅ Built: dist/Daedalus.app"
echo "   Launch:  open dist/Daedalus.app"
echo "   (unsigned — first launch: right-click → Open, or: xattr -dr com.apple.quarantine dist/Daedalus.app)"
