"""Start the desktop app.

    python -m jobmatch.app                     # native window, data in the user's app folder
    python -m jobmatch.app --browser --host 0.0.0.0   # preview in a browser (Docker), repo data

Where data lives:
- native (the packaged .app / .exe): the user data root (app_paths.user_data_root), with
  data/, exports/ and .env;
- --browser: the repo layout (config/.. as today), so a preview uses the same data as the CLI;
- --data-root PATH: any folder, for either mode.

`--check` opens no window: it loads the bundled config, NiceGUI's web files and every module
the app needs, writes the result to <data root>/check.txt and exits 0 (ok) or 1. The build
machines run it on the finished .app / .exe, so a missing piece fails the build instead of
the user's first launch.
"""

import argparse
import importlib
import multiprocessing
import sys
import traceback
from pathlib import Path

from jobmatch import secrets_file
from jobmatch.app_paths import bundled_config_dir, ensure_layout, env_file, user_data_root
from jobmatch.config import (
    load_canada_locations,
    load_company_aliases,
    load_contract_phrases,
    load_industries,
    load_normalization_rules,
    load_professions,
    load_settings,
    load_skills_taxonomy,
    load_sources,
)
from jobmatch.logging_setup import configure_logging

# Imported lazily by the app (or dynamically by its libraries), so a frozen build can miss
# them without failing at start-up: --check imports each one.
CHECK_MODULES = (
    "jobmatch.app.ui",
    "jobmatch.app.service",
    "jobmatch.pipeline",
    "jobmatch.llm.gemini",
    "jobmatch.sources.web",
    "jobmatch.sources.xlsx",
    "jobmatch.sources.saved_files",
    "nicegui",
    "webview",
    "uvicorn",
    "lxml.etree",
    "pypdf",
    "rapidfuzz",
    "ftfy",
    "bs4",
)


def _say(text: str) -> None:
    """Print when there is a console (a windowed Windows build has none)."""
    if sys.stdout is not None:
        print(text, flush=True)


def run_check(config_dir: Path, root: Path) -> int:
    """Load everything the app needs without opening a window. 0 = ok, 1 = a problem."""
    lines: list[str] = [f"frozen: {getattr(sys, 'frozen', False)}", f"config: {config_dir}"]
    ok = True
    try:
        settings = load_settings(config_dir, data_root=root)
        for loader in (
            load_skills_taxonomy, load_contract_phrases, load_company_aliases,
            load_normalization_rules, load_canada_locations, load_industries, load_sources,
        ):  # fmt: skip
            loader(config_dir)
        if load_professions(config_dir) is None:
            raise FileNotFoundError("professions.yaml is missing from the bundle")
        lines.append(f"settings: ok (model {settings.llm.model if settings.llm else '-'})")
        from jobmatch.storage.loader import create_loader

        lines.append(f"storage: {create_loader(settings).backend}")
        for name in CHECK_MODULES:
            importlib.import_module(name)
        lines.append(f"modules: {len(CHECK_MODULES)} ok")
        import nicegui

        static = Path(nicegui.__file__).parent / "static"
        if not (static / "nicegui.css").exists():
            raise FileNotFoundError(f"NiceGUI web files missing: {static}")
        lines.append("nicegui web files: ok")
        from jobmatch.app.service import AppService

        AppService(settings, env_file(root), environ={})
        lines.append("app service: ok")
    except Exception:  # every problem is reported, then the check fails
        ok = False
        lines.append("FAILED:\n" + traceback.format_exc())
    lines.append("CHECK OK" if ok else "CHECK FAILED")
    report = "\n".join(lines)
    (root / "check.txt").write_text(report + "\n", encoding="utf-8")
    _say(report)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jobmatch-app", description=__doc__)
    parser.add_argument("--browser", action="store_true", help="Serve in a browser tab instead.")
    parser.add_argument("--data-root", type=Path, help="Folder holding data/, exports/, .env.")
    parser.add_argument("--host", default="127.0.0.1", help="Browser mode: address to listen on.")
    parser.add_argument(
        "--port", type=int, help="Default: 8080 in a browser, any free port natively."
    )
    parser.add_argument("--check", action="store_true", help="Check the install and exit.")
    args = parser.parse_args(argv)
    native = not args.browser

    config_dir = bundled_config_dir()
    root = args.data_root or (user_data_root() if native else None)
    if root is not None:
        ensure_layout(root)
        env_path = env_file(root)
    else:
        env_path = config_dir.parent / ".env"
    if args.check:
        raise SystemExit(run_check(config_dir, root or ensure_layout(config_dir.parent)))
    secrets_file.load_into_environ(env_path)  # names only are returned; values never printed
    settings = load_settings(config_dir, data_root=root)
    configure_logging(settings.logging.level)

    from nicegui import ui

    from jobmatch.app.service import AppService
    from jobmatch.app.ui import build_root

    service = AppService(settings, env_path)
    ui.run(
        build_root(service, native=native),
        title="jobmatch",
        native=native,
        window_size=(1200, 860) if native else None,
        reload=False,
        show=False,
        host=None if native else args.host,
        port=args.port or (None if native else 8080),
        show_welcome_message=not native,
        uvicorn_logging_level="warning",
    )


if __name__ in {"__main__", "__mp_main__"}:
    multiprocessing.freeze_support()  # PyInstaller: the native window runs in a child process
    if __name__ == "__main__":
        main()
