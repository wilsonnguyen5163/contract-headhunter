"""The desktop app's .env reader and writer. Fake values only."""

import os
import stat
from pathlib import Path

import pytest

from jobmatch.secrets_file import (
    EnvFileError,
    load_into_environ,
    read_env_file,
    remove_value,
    set_value,
)

FAKE = "AIzaFAKE-not-a-real-key_123"


def test_reads_compose_style_lines(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "﻿# comment\n"
        "\n"
        f"GEMINI_API_KEY={FAKE}\n"
        "export JOBMATCH_CONFIG_DIR=config\n"
        'QUOTED="a value with # and \\"quotes\\""\n'
        "SINGLE='taken \\n literally'\n"
        "INLINE=plain # trailing comment\n"
        "not a setting line\n"
        "1BAD=x\n",
        encoding="utf-8",
    )
    assert read_env_file(path) == {
        "GEMINI_API_KEY": FAKE,
        "JOBMATCH_CONFIG_DIR": "config",
        "QUOTED": 'a value with # and "quotes"',
        "SINGLE": "taken \\n literally",
        "INLINE": "plain",
    }


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "nope.env") == {}
    assert remove_value(tmp_path / "nope.env", "X") is False


def test_load_keeps_existing_variables_unless_told(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(f"GEMINI_API_KEY={FAKE}\nOTHER=1\n", encoding="utf-8")
    environ = {"OTHER": "already"}
    assert load_into_environ(path, environ) == ["GEMINI_API_KEY"]  # names only, never values
    assert environ == {"OTHER": "already", "GEMINI_API_KEY": FAKE}
    assert load_into_environ(path, environ, override=True) == ["GEMINI_API_KEY", "OTHER"]
    assert environ["OTHER"] == "1"


def test_set_value_adds_replaces_and_keeps_other_lines(tmp_path: Path) -> None:
    path = tmp_path / "app" / ".env"
    set_value(path, "GEMINI_API_KEY", FAKE)  # creates the folder and file
    assert read_env_file(path) == {"GEMINI_API_KEY": FAKE}

    path.write_text(
        "# Clé Gemini (gratuite)\nGEMINI_API_KEY=old\nGEMINI_API_KEY=older\nKEEP=1\n",
        encoding="utf-8",
    )
    set_value(path, "GEMINI_API_KEY", 'new "quoted" value')
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Clé Gemini (gratuite)\n")  # comments kept
    assert text.count("GEMINI_API_KEY=") == 1  # duplicates dropped
    assert read_env_file(path) == {"GEMINI_API_KEY": 'new "quoted" value', "KEEP": "1"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_written_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("KEEP=1\n", encoding="utf-8")
    path.chmod(0o644)
    set_value(path, "GEMINI_API_KEY", FAKE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_bad_names_and_multiline_values_are_refused_without_echoing_the_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    with pytest.raises(EnvFileError, match="not a valid variable name"):
        set_value(path, "BAD-NAME", "x")
    with pytest.raises(EnvFileError, match="one line") as exc:
        set_value(path, "GEMINI_API_KEY", f"{FAKE}\nINJECTED=1")
    assert FAKE not in str(exc.value)
    assert not path.exists()


def test_remove_value(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(f"GEMINI_API_KEY={FAKE}\nKEEP=1\n", encoding="utf-8")
    assert remove_value(path, "GEMINI_API_KEY") is True
    assert read_env_file(path) == {"KEEP": "1"}
    assert remove_value(path, "GEMINI_API_KEY") is False
