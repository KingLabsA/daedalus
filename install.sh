#!/usr/bin/env bash
# Hermes Ultimate installer — local-first coding assistant.
#   ./install.sh          (from a cloned repo)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
      PY="$candidate"; break
    fi
  fi
done
[ -n "$PY" ] || { echo "ERROR: Python >= 3.10 required."; exit 1; }
echo "Using $PY ($($PY --version))"

echo "Installing Hermes (editable) ..."
"$PY" -m pip install --quiet --user -e "$HERE" || "$PY" -m pip install --quiet -e "$HERE"

if command -v hermes >/dev/null 2>&1; then
  hermes version
else
  BIN="$($PY -m site --user-base)/bin"
  echo "Installed, but '$BIN' is not on your PATH."
  echo "Add:  export PATH=\"$BIN:\$PATH\"   to your ~/.zshrc"
  "$BIN/hermes" version 2>/dev/null || true
fi

echo
echo "Next steps:"
echo "  hermes doctor    # scan this machine (deps + which providers are LIVE)"
echo "  hermes           # terminal UI"
echo "  hermes web       # web IDE in your browser"
echo
echo "Local-first: start Ollama and/or launch FreeLLMAPI (localhost:3002) — no paid APIs needed."
