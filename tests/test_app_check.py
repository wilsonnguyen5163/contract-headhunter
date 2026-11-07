"""The desktop app's --check (run by the build machines on the finished .app / .exe)."""

from pathlib import Path

import pytest

from conftest import CONFIG_DIR
from jobmatch.app.__main__ import main, run_check


def test_check_passes_with_the_repo_config(tmp_path: Path) -> None:
    assert run_check(CONFIG_DIR, tmp_path) == 0
    report = (tmp_path / "check.txt").read_text(encoding="utf-8")
    assert "CHECK OK" in report and "nicegui web files: ok" in report


def test_check_fails_and_explains_when_config_is_missing(tmp_path: Path) -> None:
    broken = tmp_path / "config"
    broken.mkdir()
    assert run_check(broken, tmp_path) == 1
    report = (tmp_path / "check.txt").read_text(encoding="utf-8")
    assert "CHECK FAILED" in report and "settings.yaml" in report


def test_check_flag_exits_with_the_result(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--check", "--data-root", str(tmp_path / "app")])
    assert exc.value.code == 0
    assert (tmp_path / "app" / "data").is_dir()  # the app folder layout was created
