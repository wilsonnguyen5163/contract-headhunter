# PyInstaller recipe for the jobmatch desktop app (doc/DEVELOPMENT.md, Desktop app).
#
#   uv run --no-sync pyinstaller packaging/jobmatch.spec --noconfirm --clean
#
# macOS (Intel): dist/jobmatch.app      Windows: dist/jobmatch/jobmatch.exe (a folder, zipped by CI)
#
# Bundled: the jobmatch code, NiceGUI's web files, pywebview's files and config/ (read-only).
# Never bundled: data/, exports/, .env. Contractor data and the Gemini key stay on the user's
# machine, in the app's own folder (app_paths.user_data_root).
# Built on GitHub Actions (.github/workflows/build-app.yml); PyInstaller cannot cross-build,
# so each platform builds on its own runner.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is set by PyInstaller)
NAME = "jobmatch"

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
        bundle_identifier="org.jobmatch.desktop",
        info_plist={
            "CFBundleDisplayName": "jobmatch",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "10.15",
        },
    )
