"""End-to-end manual intake: paste files -> raw store -> normalize -> dedupe -> jobs.csv."""

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from conftest import CONFIG_DIR, SAMPLES_DIR
from jobmatch.cli import app
from jobmatch.ingest import ingest_paste_files
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.schemas import JobStatus
from jobmatch.storage.csv_store import CsvJobRepository, CsvRunRepository, CsvStore
from jobmatch.storage.raw_store import RawStore

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)

EN_CONTRACT = """url: https://www.linkedin.com/jobs/view/4012345678/
title: Senior Business Analyst (12-month contract)
company: Example Insurance Inc.
location: Toronto, ON (Hybrid)
posted: 2026-10-18
---
Example Insurance needs a Senior Business Analyst for a 12-month contract.
Requirements
- Strong SQL and process mapping (BPMN)
"""

FR_STUB = """url: https://www.linkedin.com/jobs/search/?currentJobId=4019876543
title: Gestionnaire de projet TI (mandat)
company: Exemple Services Ltée
location: Québec, QC
---
"""

FR_FULL_SAME_URL = FR_STUB + (
    "Nous recherchons un gestionnaire de projet TI pour un mandat de 6 mois.\n"
    "Exigences :\n- Gestion de projet et budget\n"
)

PERMANENT = """url: https://careers.example.ca/jobs/77?utm_source=x
title: Data Analyst
company: Example Retail Ltd.
location: Vancouver, BC
---
Permanent full-time position with benefits. You will analyse sales data with SQL and Power BI.
"""

BROKEN = "title: no url and no separator\n"


def write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def run(normalizer: Normalizer, data_dir: Path, *paths: Path, dry_run: bool = False):
    return ingest_paste_files(
        list(paths),
        data_dir=data_dir,
        lock_timeout=5,
        normalizer=normalizer,
        now=NOW,
        dry_run=dry_run,
    )


def test_ingest_folder_end_to_end(normalizer: Normalizer, tmp_path: Path) -> None:
    inbox, data_dir = tmp_path / "inbox", tmp_path / "data"
    write(inbox, "_TEMPLATE.txt", "url:\ntitle:\n---\n")
    write(inbox, "a_en_contract.txt", EN_CONTRACT)
    write(inbox, "b_fr_stub.txt", FR_STUB)
    write(inbox, "c_permanent.txt", PERMANENT)
    write(inbox, "d_broken.txt", BROKEN)

    report = run(normalizer, data_dir, inbox)

    by_file = {Path(o.file).name: o for o in report.outcomes}
    assert by_file["_TEMPLATE.txt"].outcome == "skipped"
    assert by_file["a_en_contract.txt"].outcome == "new"
    assert by_file["a_en_contract.txt"].job_id == "manual_linkedin:4012345678"
    assert by_file["b_fr_stub.txt"].outcome == "needs_details"
    assert by_file["c_permanent.txt"].outcome == "filtered"
    assert by_file["c_permanent.txt"].detail == "not_contract"
    assert by_file["d_broken.txt"].outcome == "error"

    store = CsvStore(data_dir, lock_timeout=5)
    jobs = {j.job_id: j for j in CsvJobRepository(store).list_all()}
    assert len(jobs) == 3
    assert jobs["manual_linkedin:4012345678"].skills[:2] == ["business analysis", "sql"]
    assert len(RawStore(data_dir / "raw", lock_timeout=5).read("manual_linkedin")) == 2
    [run_row] = CsvRunRepository(store).list_all()
    assert (run_row.new_jobs, run_row.filtered_out, run_row.errors) == (2, 1, 1)


def test_rerun_changes_nothing(normalizer: Normalizer, tmp_path: Path) -> None:
    inbox, data_dir = tmp_path / "inbox", tmp_path / "data"
    write(inbox, "a.txt", EN_CONTRACT)
    run(normalizer, data_dir, inbox)
    jobs_before = (data_dir / "jobs.csv").read_bytes()

    report = run(normalizer, data_dir, inbox)

    assert [o.outcome for o in report.outcomes] == ["already_stored"]
    assert (data_dir / "jobs.csv").read_bytes() == jobs_before
    assert len(RawStore(data_dir / "raw", lock_timeout=5).read("manual_linkedin")) == 1


def test_stub_completed_later_by_pasting_text(normalizer: Normalizer, tmp_path: Path) -> None:
    inbox, data_dir = tmp_path / "inbox", tmp_path / "data"
    stub_file = write(inbox, "fr.txt", FR_STUB)
    run(normalizer, data_dir, stub_file)

    write(inbox, "fr.txt", FR_FULL_SAME_URL)  # same file, text pasted under ---
    report = run(normalizer, data_dir, stub_file)

    assert report.outcomes[0].outcome == "filled_stub"
    [job] = CsvJobRepository(CsvStore(data_dir, lock_timeout=5)).list_all()
    assert job.status is JobStatus.NEW
    assert job.duration_months == 6.0
    assert list((data_dir / "backups").glob("jobs.*.csv"))  # rewrite took a backup


def test_same_job_from_two_sites_is_marked_duplicate(
    normalizer: Normalizer, tmp_path: Path
) -> None:
    inbox, data_dir = tmp_path / "inbox", tmp_path / "data"
    write(inbox, "a.txt", EN_CONTRACT)
    write(
        inbox,
        "b.txt",
        EN_CONTRACT.replace(
            "https://www.linkedin.com/jobs/view/4012345678/",
            "https://careers.example-insurance.ca/jobs/ba-12",
        ),
    )
    report = run(normalizer, data_dir, inbox)
    assert [o.outcome for o in report.outcomes] == ["new", "duplicate"]
    assert report.outcomes[1].detail == "same job as manual_linkedin:4012345678"


def test_dry_run_writes_nothing(normalizer: Normalizer, tmp_path: Path) -> None:
    inbox, data_dir = tmp_path / "inbox", tmp_path / "data"
    write(inbox, "a.txt", EN_CONTRACT)
    report = run(normalizer, data_dir, inbox, dry_run=True)
    assert report.outcomes[0].outcome == "new"
    assert not (data_dir / "jobs.csv").exists()
    assert not (data_dir / "raw").exists()


def test_cli_ingests_the_sample_files(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "--config-dir", str(CONFIG_DIR),
            "ingest-manual", str(SAMPLES_DIR / "manual"),
            "--data-dir", str(tmp_path / "data"),
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "example_linkedin_contract.txt" in result.output
    assert "1 needs details" in result.output  # the stub; the full sample may age into "stale"


def test_empty_files_are_skipped_not_errors(normalizer: Normalizer, tmp_path: Path) -> None:
    inbox, data_dir = tmp_path / "inbox", tmp_path / "data"
    write(inbox, "a_empty.txt", "")
    write(inbox, "b_blank.txt", "  \n\n\t\n")
    write(inbox, "c.txt", EN_CONTRACT)
    report = run(normalizer, data_dir, inbox)
    by_file = {Path(o.file).name: o for o in report.outcomes}
    assert by_file["a_empty.txt"].outcome == "skipped"
    assert by_file["b_blank.txt"].detail == "empty file"
    assert by_file["c.txt"].outcome == "new"
    assert report.count("error") == 0
