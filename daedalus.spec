# PyInstaller spec — bundles Daedalus into a standalone macOS .app / binary.
# Build:  pyinstaller daedalus.spec   (see build_app.sh)
# Produces dist/Daedalus.app — self-contained: Python + agent + bundled UI.
from pathlib import Path

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "hermes_webui"), "hermes_webui"),   # bundled web UI
    (str(ROOT / "core"), "core"),
]

hidden = [
    "webview", "websockets", "openai", "requests", "rich",
    "agent_ultimate", "hermes_cli", "desktop_app",
]

a = Analysis(
    ["desktop_app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    excludes=["playwright", "pyautogui", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Daedalus",
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Daedalus")

app = BUNDLE(
    coll, name="Daedalus.app", icon=str(ROOT / "docs" / "logo.png"),
    bundle_identifier="ai.daedalus.desktop",
    info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0"},
)
