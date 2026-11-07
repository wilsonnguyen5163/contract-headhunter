"""Desktop-app folders and the settings data-root override. No real home folder is touched."""

import sys
from pathlib import Path

import pytest

from conftest import CONFIG_DIR, PROJECT_ROOT
from jobmatch.app_paths import bundled_config_dir, ensure_layout, env_file, user_data_root
from jobmatch.config import DATA_DIR_ENV, load_settings

HOME = Path("/home/someone")


def test_mac_folder() -> None:
    root = user_data_root("darwin", env={}, home=HOME)
    assert root == HOME / "Library" / "Application Support" / "jobmatch"


def test_windows_folder_uses_appdata() -> None:
    appdata = r"C:\Users\Camille\AppData\Roaming"
    assert user_data_root("win32", env={"APPDATA": appdata}, home=HOME) == (
        Path(appdata) / "jobmatch"
    )
    # No APPDATA (unusual): the default location under the home folder.
    assert user_data_root("win32", env={}, home=HOME) == (HOME / "AppData" / "Roaming" / "jobmatch")


def test_other_systems_follow_xdg() -> None:
    assert user_data_root("linux", env={}, home=HOME) == HOME / ".local" / "share" / "jobmatch"
    assert user_data_root("linux", env={"XDG_DATA_HOME": "/x"}, home=HOME) == Path("/x/jobmatch")


def test_config_comes_from_the_bundle_when_frozen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert bundled_config_dir() == CONFIG_DIR  # running from the repo
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert bundled_config_dir() == tmp_path / "config"


def test_layout_is_created_once(tmp_path: Path) -> None:
    root = ensure_layout(tmp_path / "jobmatch")
    ensure_layout(root)  # second call is a no-op
    assert (root / "data").is_dir() and (root / "exports").is_dir()
    assert env_file(root) == root / ".env"


def test_settings_paths_follow_the_data_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    default = load_settings(CONFIG_DIR)
    assert default.paths.data_dir == PROJECT_ROOT / "data"  # CLI and Docker unchanged

    app = load_settings(CONFIG_DIR, data_root=tmp_path)
    assert app.paths.data_dir == tmp_path / "data"
    assert app.paths.exports_dir == tmp_path / "exports"
    assert app.paths.contractors_csv == tmp_path / "data" / "contractors" / "contractors.csv"
    assert app.config_dir == CONFIG_DIR  # config still read from where it is

    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "from-env"))
    assert load_settings(CONFIG_DIR).paths.data_dir == tmp_path / "from-env" / "data"
    # An explicit argument wins over the environment.
    assert load_settings(CONFIG_DIR, data_root=tmp_path).paths.data_dir == tmp_path / "data"
