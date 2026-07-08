#!/usr/bin/env bash
# Refresh the pip-bundled web UI from the frontend build. Run before every release.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd desktop && npm run build)
find hermes_webui -type f ! -name "__init__.py" -delete
find hermes_webui -type d -empty -delete 2>/dev/null || true
cp -R desktop/dist/. hermes_webui/
echo "hermes_webui synced"
