"""A jobs CSV picked again after a fix (user decisions 2026-11-11): a row that fails the checks
is dropped and reported, the rest of the file goes in, and the file leaves the inbox; the
fixed row goes in when the file is picked again. A stored job is never considered again,
even with changed details (to change one: delete it, then import it again). Fake Gemini, no
network."""

import csv
import io
from pathlib import Path

from jobmatch.app.service import AppService
from jobmatch.config import Settings
from jobmatch.sources.job_csv import COLUMNS
from test_talent import MarkerGemini, picked, profile

ANALYST = {
    "url": "https://www.example.ca/jobs/1", "job_title": "Data Analyst (contract)",
    "company": "Example Inc.", "contact_person": "Jordan Lee",
    "contact_email": "jordan.lee@example.ca", "location": "Toronto, ON",
    "work_mode": "hybrid", "responsibility": "Build Power BI dashboards",
    "qualification": "SQL",
}  # fmt: skip
ANALYSTE = {
    "url": "https://www.exemple.qc.ca/emplois/2", "job_title": "Analyste de données (mandat)",
    "company": "Ville de Québec", "location": "Québec, QC",
    "work_mode": "in-person",  # not a work mode the file accepts: this row is dropped
    "responsibility": "Tableaux de bord Power BI", "qualification": "Français",
}  # fmt: skip


def csv_bytes(*rows: dict[str, str]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def test_fix_and_pick_again(app_settings: Settings, tmp_path: Path) -> None:
    fake = MarkerGemini()
    service = AppService(
        app_settings, tmp_path / "app.env", provider_factory=lambda: fake, environ={}
    )
    contractors = picked(tmp_path, [profile(1), profile(2)])
    service.pick_contractor_file(contractors.name, path=contractors)

    def ingest(*rows: dict[str, str]) -> tuple[list[str], list[str]]:
        """Pick a jobs file with these rows, then Find matches: each row's outcome, the log."""
        service.add_jobs_file("jobs.csv", csv_bytes(*rows))
        lines: list[str] = []
        report = service.run(echo=lines.append, confirm=lambda c: True)
        return [o.outcome for i in report.ingest for o in i.outcomes], lines

    # 1. The bad row is dropped and reported; the good row goes in; the file leaves the inbox.
    outcomes, lines = ingest(ANALYST, ANALYSTE)
    assert outcomes == ["new", "invalid"]
    assert any("'in-person': use on-site, hybrid or remote" in line for line in lines)
    assert any("row(s) dropped" in line for line in lines)
    inbox = service.data_dir / "inbox" / "manual"
    assert list(inbox.glob("*.csv")) == []  # moved to done/: the bad row never comes back
    per_job = fake.calls  # screening, then review
    assert per_job >= 1
    jobs = service._loader().jobs
    assert [j.title_raw for j in jobs.list_all()] == ["Data Analyst (contract)"]

    # 2. Fixed and picked again: the fixed row goes in; the unchanged row is ignored.
    fixed = {**ANALYSTE, "work_mode": "en personne"}
    assert ingest(ANALYST, fixed)[0] == ["already_stored", "new"]
    assert fake.calls == 2 * per_job  # the new job only
    stored = {j.title_raw: j for j in jobs.list_all()}
    assert stored["Analyste de données (mandat)"].remote_type.value == "onsite"

    # 3. A stored job with changed details: still ignored, nothing sent.
    changed = {**ANALYST, "responsibility": "Train the finance team"}
    assert ingest(changed)[0] == ["already_stored"]
    assert fake.calls == 2 * per_job
    [job] = [j for j in jobs.list_all() if j.title_raw == "Data Analyst (contract)"]
    assert job.responsibilities == ["Build Power BI dashboards"]
