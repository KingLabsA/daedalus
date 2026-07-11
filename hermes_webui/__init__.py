"""Bundled Daedalus web UI (built from desktop/) so `daedalus web` works from pip.
Refresh before release: scripts/sync_webui.sh
"""

from pathlib import Path


def dist_path() -> Path:
    return Path(__file__).parent
