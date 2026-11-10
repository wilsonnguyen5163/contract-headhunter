"""Jobs entered by a reviewer (user decisions 2026-11-10): a jobs CSV file, or the app's form
(same format). Stored as given, without the normaliser: cells are only cleaned of the
formatting copying drags along. English and French rows. No network, no Gemini call."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobmatch.app.service import AppError, AppService
from jobmatch.config import Settings
from jobmatch.llm.payload import job_card, render_job
from jobmatch.schemas import Commitment, JobStatus, PayUnit, RemoteType
from jobmatch.sources.job_csv import (
    COLUMNS,
    JobCsvError,
    clean_cell,
    clean_items,
    parse_number,
    parse_pay,
    parse_posted,
    read_job_csv,
)
from test_app_ui import run_ui
from test_talent import MarkerGemini

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
HEADER = ",".join(COLUMNS)


def write(tmp_path: Path, body: str, name: str = "jobs.csv") -> Path:
    path = tmp_path / name
    path.write_text(HEADER + "\n" + body, encoding="utf-8-sig")
    return path


def test_cleaning_copied_text() -> None:
    assert clean_cell("  <b>Data&nbsp;Analyst</b>​  (contract) ") == "Data Analyst (contract)"
    assert clean_cell("**Toronto**,\nON") == "Toronto, ON"
    assert clean_items("• Build dashboards\n\n  - Write **SQL**  \n1. Document | 2) Train") == [
        "Build dashboards", "Write SQL", "Document", "Train",
    ]  # fmt: skip
    assert clean_items("● Animer les ateliers\r\n– Rédiger les rapports") == [
        "Animer les ateliers", "Rédiger les rapports",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("text", "value"),
    [("85", 85), ("37.5", 37.5), ("37,5", 37.5), ("$85,000", 85000), ("95 000 $", 95000),
     ("CAD 90", 90)],
)  # fmt: skip
def test_numbers(text: str, value: float) -> None:
    assert parse_number(text) == value


@pytest.mark.parametrize(
    ("text", "day"),
    [("2026-11-08", 8), ("2026/11/08", 8), ("8 Nov 2026", 8), ("November 8, 2026", 8),
     ("8 novembre 2026", 8), ("1er novembre 2026", 1), ("", None)],
)  # fmt: skip
def test_posted_dates(text: str, day: int | None) -> None:
    got = parse_posted(text)
    assert (got.day if got else None) == day


def test_numeric_date_is_refused() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        parse_posted("03/04/2026")


def test_rows_stored_as_given(tmp_path: Path) -> None:
    path = write(tmp_path, (
        'https://www.example.ca/jobs/1?utm_source=x,Data Analyst (6-month contract),Example Inc.,'
        '"Toronto, ON",Hybrid,40,$90,hourly,2026-11-08,"• Build Power BI dashboards\n• Write SQL",'
        '"3+ years of SQL | Power BI"\n'
        "https://www.exemple.qc.ca/emplois/2,Analyste d'affaires (permanent),Ville de Québec,"
        'Québec QC,sur place,"37,5",95 000 $,salaire,8 novembre 2026,"Documenter les processus",'
        '"Français et anglais. Écrire à rh@ville.qc.ca"\n'
        ",Missing link,,,,,,,,,\n"
        "https://www.example.ca/jobs/3,Bad date,,,,,,,03/04/2026,,\n"
        "https://www.example.ca/jobs/4,Bad terms,,,in the office,40 h/week,85,,,,\n"
        'https://www.example.ca/jobs/5,Too many hours,,,remote,95,"85,000",salary,,,\n'
    ))  # fmt: skip
    rows = read_job_csv(path, NOW)
    assert [r.row for r in rows] == [2, 3, 4, 5, 6, 7]
    first, second = rows[0].job, rows[1].job
    assert first is not None and second is not None
    assert (first.title_raw, first.company_raw, first.location_raw) == (
        "Data Analyst (6-month contract)", "Example Inc.", "Toronto, ON",
    )  # fmt: skip
    assert first.url == "https://www.example.ca/jobs/1"  # tracking removed: same job ID as a link
    assert first.responsibilities == ["Build Power BI dashboards", "Write SQL"]
    assert first.requirements == ["3+ years of SQL", "Power BI"]
    assert (first.remote_type, first.hours_per_week, first.status) == (
        RemoteType.HYBRID, 40, JobStatus.NEW,
    )  # fmt: skip
    assert (first.pay_min, first.pay_max, first.pay_unit, first.pay_currency) == (
        90, 90, PayUnit.HOUR, "CAD",
    )  # fmt: skip
    assert first.posted_at == datetime(2026, 11, 8, tzinfo=UTC)
    # No normaliser: no filters (even "permanent" in the title), no title parsing.
    assert second.status is JobStatus.NEW and second.title_norm is None
    assert (second.remote_type, second.hours_per_week) == (RemoteType.ONSITE, 37.5)
    assert (second.pay_min, second.pay_unit) == (95000, PayUnit.YEAR)
    assert second.contact_email == "rh@ville.qc.ca"
    assert "url: is empty" in (rows[2].error or "")
    assert "ambiguous" in (rows[3].error or "")
    terms = rows[4].error or ""
    assert "work_mode: 'in the office': use on-site, hybrid or remote" in terms
    assert rows[5].job is None and "between 1 and 80" in (rows[5].error or "")


@pytest.mark.parametrize(
    ("text", "kept"),
    [("90", 90), ("85–95", 85), ("85 - 95", 85), ("$85,000 to $95,000", 85000),
     ("90 000 $ à 110 000 $", 90000), ("95-85", 85)],
)  # fmt: skip
def test_pay_range_keeps_the_lower_end(text: str, kept: float) -> None:
    assert parse_pay(text) == kept


def test_hours_give_full_or_part_time(tmp_path: Path) -> None:
    path = write(tmp_path, (
        "https://x.ca/j/1,Analyst,,,,40,,,,,\n"
        "https://x.ca/j/2,Bilingual (Fr/Eng),,,,20,,,,,\n"
        "https://x.ca/j/3,No hours,,,,,,,,,\n"
    ))  # fmt: skip
    rows = read_job_csv(path, NOW, full_time_hours=30)
    assert [r.job.commitment for r in rows if r.job] == [
        Commitment.FULL_TIME, Commitment.PART_TIME, Commitment.UNKNOWN,
    ]  # fmt: skip


def test_pay_needs_a_type(tmp_path: Path) -> None:
    path = write(tmp_path, "https://x.ca/j/1,Analyst,,,remote,40 h/week,85,,,,\n")
    [row] = read_job_csv(path, NOW)
    assert row.job is None and "say whether the pay is salary or hourly" in (row.error or "")
    path = write(tmp_path, "https://x.ca/j/1,Analyst,,,remote,40 h/week,,,,,\n")
    [row] = read_job_csv(path, NOW)
    assert row.job is not None and row.job.hours_per_week == 40  # "40 h/week" reads as 40


def test_hours_and_work_mode_go_to_gemini_but_not_pay(
    app_settings: Settings, tmp_path: Path
) -> None:
    assert app_settings.llm is not None
    path = write(tmp_path, "https://x.ca/j/1,Analyst,,,remote,20,135,hourly,,Build reports,SQL\n")
    [row] = read_job_csv(path, NOW)
    assert row.job is not None
    text = render_job(job_card(row.job, app_settings.llm.payload))
    assert "Hours: 20 per week" in text and "remote" in text and "135" not in text


def test_bad_header_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "jobs.csv"
    path.write_text("link,title\nhttps://x.ca,Analyst\n", encoding="utf-8")
    with pytest.raises(JobCsvError, match=r"jobs.csv row 1: missing column\(s\): url, job_title"):
        read_job_csv(path, NOW)
    path.write_bytes("url,job_title\nhttps://x.ca,Analyste\xe9\n".encode("cp1252"))
    with pytest.raises(JobCsvError, match="CSV UTF-8"):
        read_job_csv(path, NOW)


@pytest.fixture
def service(app_settings: Settings, tmp_path: Path) -> AppService:
    return AppService(app_settings, tmp_path / "app.env", provider_factory=MarkerGemini, environ={})


def test_csv_file_and_form_go_in_on_ingest(service: AppService, tmp_path: Path) -> None:
    csv_file = write(tmp_path, (
        "https://www.example.ca/jobs/1,Data Analyst,Example Inc.,,,,,,,Build dashboards,SQL\n"
        ",No link,,,,,,,,,\n"
    ))  # fmt: skip
    info = service.add_jobs_file(csv_file.name, csv_file.read_bytes())
    assert (info.jobs, info.problems) == (1, ["Row 3: url: is empty"])

    with pytest.raises(AppError, match="Job title: is empty"):
        service.add_job_entry({"url": "https://x.ca/j/9", "job_title": "  "})
    entry = service.add_job_entry({
        "url": "https://www.exemple.qc.ca/emplois/2", "job_title": "Analyste (mandat)",
        "company": "Ville de Québec", "responsibility": "- Documenter\n\n- Animer",
        "posted_date": "2026-11-05", "work_mode": "hybrid", "hours_per_week": "35",
        "pay": "80", "pay_type": "hourly",
    })  # fmt: skip
    assert entry.responsibility == ["Documenter", "Animer"]
    [pending] = service.pending_entries()
    assert (pending.title, pending.company) == ("Analyste (mandat)", "Ville de Québec")

    report = service.run(echo=lambda s: None, confirm=lambda c: True, match=False)
    outcomes = sorted(o.outcome for i in report.ingest for o in i.outcomes)
    assert outcomes == ["error", "new", "new"]
    loader = service._loader()
    stored = {j.title_raw: j for j in loader.jobs.list_all()}
    assert set(stored) == {"Data Analyst", "Analyste (mandat)"}
    assert stored["Analyste (mandat)"].hours_per_week == 35
    assert service.pending_entries() == []  # the form entry is stored and left the inbox
    inbox = service.data_dir / "inbox" / "manual"
    assert [p.name.endswith("-jobs.csv") for p in inbox.glob("*.csv")] == [True]  # bad row: kept

    # Entered again: already stored, nothing changes.
    service.add_job_entry({"url": "https://www.exemple.qc.ca/emplois/2/", "job_title": "Autre"})
    report = service.run(echo=lambda s: None, confirm=lambda c: True, match=False)
    assert "already_stored" in [o.outcome for i in report.ingest for o in i.outcomes]


def test_template_reads_back(service: AppService) -> None:
    rows = read_job_csv(service.job_template(), NOW)
    assert [r.error for r in rows] == [None, None]
    assert rows[1].job is not None and rows[1].job.company_raw == "Ville de Québec"
    assert rows[1].job.pay_unit is PayUnit.YEAR


def test_form_on_screen(service: AppService) -> None:
    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/")
        user.find(marker="add-job").click()
        await user.should_see("Add a job by hand")
        await user.should_see(marker="job-field:responsibility")
        user.find(marker="job-field:url").type("https://www.example.ca/jobs/7")
        user.find(marker="job-field:job_title").type("Rédacteur (contrat)")
        user.find(marker="job-field:hours_per_week").type("20")
        user.find(marker="job-field:pay").type("85–95")
        user.find(marker="job-save").click()
        await user.should_see("Entered by hand (1)")
        await user.should_see("Rédacteur (contrat)")
        [entry] = service.pending_entries()
        user.find(marker=f"remove-entry:{entry.name}").click()
        await user.should_see("Remove Rédacteur (contrat)?")
        user.find(marker="delete-confirm").click()
        await user.should_not_see("Entered by hand")

    run_ui(service, scenario)
    assert service.pending_entries() == []


def test_title_shows_full_or_part_time(service: AppService) -> None:
    service.add_job_entry(
        {
            "url": "https://x.ca/j/5",
            "job_title": "Bilingual (Fr/Eng)",
            "hours_per_week": "20",
            "pay": "85-95",
            "pay_type": "hourly",
        }
    )
    report = service.run(echo=lambda s: None, confirm=lambda c: True, match=False)
    assert [o.outcome for i in report.ingest for o in i.outcomes] == ["new"]
    [job] = service._loader().jobs.list_all()
    assert (job.commitment, job.pay_min) == (Commitment.PART_TIME, 85)

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/jobs")
        await user.should_see("- PT", marker=f"commitment:{job.job_id}")

    run_ui(service, scenario)
