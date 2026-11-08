# PyInstaller recipe for the desktop app, "do good talent" (doc/DEVELOPMENT.md, Desktop app).
# The code package is still called jobmatch; only the app's files and title are renamed.
#
#   uv run --no-sync pyinstaller packaging/jobmatch.spec --noconfirm --clean
#
# macOS (Intel): dist/dogoodtalent.app
# Windows: dist/dogoodtalent/dogoodtalent.exe (a folder, zipped by CI)
#
# Bundled: the jobmatch code, NiceGUI's web files, pywebview's files and config/ (read-only).
# Never bundled: data/, exports/, .env. Contractor data and the Gemini key stay on the user's
# machine, in the app's own folder (app_paths.user_data_root).
# Built on GitHub Actions (.github/workflows/build-app.yml); PyInstaller cannot cross-build,
# so each platform builds on its own runner.

import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is set by PyInstaller)
NAME = "dogoodtalent"  # the executable and folder; same as app_paths.APP_FOLDER
TITLE = "do good talent"  # shown in the Mac Finder and Dock; same as app_paths.APP_TITLE
# One version number: pyproject.toml. A release tag must match it (build-app.yml).
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

datas = [(str(ROOT / "config"), "config")]
datas += collect_data_files("nicegui")  # web files (static/, elements' js)
datas += collect_data_files("webview")  # pywebview's js and platform files
for package in ("nicegui", "fastapi", "starlette", "uvicorn", "pydantic", "httpx"):
    datas += copy_metadata(package)  # importlib.metadata.version(...) at runtime

hiddenimports = (
    collect_submodules("jobmatch")
    + collect_submodules("uvicorn")  # protocol and loop modules picked by name
    + collect_submodules("engineio.async_drivers")  # socket.io driver picked by name
    + collect_submodules("nicegui.elements")
)

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "ruff", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,  # a window app: no terminal window
    upx=False,  # UPX-packed files are flagged more often by antivirus tools
    target_arch="x86_64" if sys.platform == "darwin" else None,  # the client's Intel Mac
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name=NAME,
    upx=False,
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{NAME}.app",
        bundle_identifier="org.dogoodtalent.desktop",
        info_plist={
            "CFBundleDisplayName": TITLE,
            "CFBundleName": TITLE,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.15",
        },
    )
