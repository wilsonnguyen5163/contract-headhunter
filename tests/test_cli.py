import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from conftest import CONFIG_DIR, SAMPLES_DIR
from jobmatch.cli import app

runner = CliRunner()


def invoke(*args: str):
    return runner.invoke(app, ["--config-dir", str(CONFIG_DIR), *args])


def test_samples_are_valid() -> None:
    if not SAMPLES_DIR.exists():
        pytest.skip("data/samples is not mounted")
    result = invoke("validate-data", "--data-dir", str(SAMPLES_DIR))
    assert result.exit_code == 0, result.output
    assert "All data valid." in result.output


def test_empty_data_dir_is_valid(data_dir: Path) -> None:
    result = invoke("validate-data", "--data-dir", str(data_dir))
    assert result.exit_code == 0, result.output


def test_invalid_data_fails_with_row_reference(data_dir: Path) -> None:
    (data_dir / "reviews.csv").write_text(
        "match_id,decision,reason_code,notes,reviewer,reviewed_at\n"
        "M1,maybe,,,R1,2026-11-04T00:00:00Z\n",
        encoding="utf-8",
    )
    result = invoke("validate-data", "--data-dir", str(data_dir))
    assert result.exit_code == 1
    assert "reviews.csv, row 2" in result.output


def test_run_once_needs_a_contractor_list(tmp_path: Path) -> None:
    """run-once against a temp config whose paths point into tmp_path, never the real data/."""
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    settings = yaml.safe_load((config / "settings.yaml").read_text(encoding="utf-8"))
    settings["paths"] = {
        "data_dir": str(tmp_path / "data"),
        "exports_dir": str(tmp_path / "exports"),
        "contractors_csv": str(tmp_path / "data" / "contractors" / "contractors.csv"),
        "learned_skills": str(tmp_path / "data" / "contractors" / "skills_learned.yaml"),
    }
    (config / "settings.yaml").write_text(yaml.safe_dump(settings), encoding="utf-8")
    result = runner.invoke(app, ["--config-dir", str(config), "run-once"])
    assert result.exit_code == 1, result.output
    assert "no contractor list" in result.output
    assert not (tmp_path / "data" / "llm_matches.csv").exists()
