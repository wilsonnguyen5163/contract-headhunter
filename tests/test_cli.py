from pathlib import Path

import pytest
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
    (data_dir / "contractor_facets.csv").write_text(
        "contractor_id,facet_id,area,years\nC9,data,Data,-3\n", encoding="utf-8"
    )
    result = invoke("validate-data", "--data-dir", str(data_dir))
    assert result.exit_code == 1
    assert "contractor_facets.csv, row 2" in result.output


def test_facet_with_unknown_contractor_is_reported(data_dir: Path) -> None:
    (data_dir / "contractor_facets.csv").write_text(
        "contractor_id,facet_id,area\nC9,data,Data\n", encoding="utf-8"
    )
    result = invoke("validate-data", "--data-dir", str(data_dir))
    assert result.exit_code == 1
    assert "unknown contractor_id 'C9'" in result.output


def test_unbuilt_commands_exit_with_code_2() -> None:
    result = invoke("run-once")
    assert result.exit_code == 2
    assert "phase 6" in result.output
