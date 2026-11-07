"""Where the desktop app keeps things.

- **User data root** (writable, survives app updates): `data/`, `exports/` and `.env` (the
  Gemini key) live here.
  - macOS: ~/Library/Application Support/jobmatch
  - Windows: %APPDATA%\\jobmatch
  - other (Linux, development): $XDG_DATA_HOME/jobmatch, else ~/.local/share/jobmatch
- **Config folder** (read-only): bundled inside the app by PyInstaller (sys._MEIPASS/config);
  when running from the repo, the repo's config/.

The CLI and Docker don't use this module; they keep resolving paths against the repo.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

APP_FOLDER = "jobmatch"
ENV_FILE = ".env"
_REPO_ROOT = Path(__file__).resolve().parents[2]  # src/jobmatch/app_paths.py -> repo


def user_data_root(
    platform: str = sys.platform,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if env is None else env
    home = home or Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_FOLDER
    if platform == "win32":
        appdata = env.get("APPDATA")
        return (Path(appdata) if appdata else home / "AppData" / "Roaming") / APP_FOLDER
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / APP_FOLDER


def bundled_config_dir() -> Path:
    """The config folder shipped with the app, or the repo's config/ when not frozen."""
    bundle = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle:
        return Path(bundle) / "config"
    return _REPO_ROOT / "config"


def env_file(root: Path) -> Path:
    return root / ENV_FILE


def ensure_layout(root: Path) -> Path:
    """Create the data root with data/ and exports/ (no-op when they exist)."""
    for sub in ("data", "exports"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
