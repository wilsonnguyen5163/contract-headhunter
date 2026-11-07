"""The desktop app's logic (app/service.py), without any UI. Fake Gemini, no network."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobmatch.app.service import (
    REVIEWER_ENV,
    AppError,
    AppService,
    classify_jobs_file,
)
from jobmatch.config import Settings
from jobmatch.llm.usage import RunCheck
from jobmatch.schemas import Decision, Job, ReasonCode
from jobmatch.secrets_file import read_env_file
from jobmatch.storage.csv_store import CsvContractorProfileRepository, CsvReviewRepository, CsvStore
from test_pipeline import add_jobs, contractor_file

NOW = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)
FAKE_KEY = "AIzaFAKE-test-only"


# ---- jobs files ----


@pytest.mark.parametrize(
    ("name", "text", "kind"),
    [
        ("links.txt", "# from alerts\nhttps://jobs.example.ca/1\nhttp://x.ca/2  # note\n", "links"),
        ("poste.txt", "url: https://emplois.example.ca/9\ntitle: Analyste\n---\nTexte", "manual"),
        ("mixed.txt", "https://x.ca/1\nSenior Analyst at Example\n", "manual"),
        ("page.html", "<html></html>", "manual"),
        ("posting.PDF", "%PDF-1.7", "manual"),
    ],
)
def test_jobs_file_kind(name: str, text: str, kind: str) -> None:
    assert classify_jobs_file(name, text.encode("utf-8")) == kind


def test_other_file_types_are_refused() -> None:
    with pytest.raises(AppError, match=".txt, .html or .pdf"):
        classify_jobs_file("jobs.docx", b"x")


def test_jobs_files_go_to_the_inbox_without_overwriting(service: AppService) -> None:
    data = b"https://jobs.example.ca/1\nhttps://jobs.example.ca/2\n# done\n"
    first = service.add_jobs_file("links.txt", data, now=NOW)
    again = service.add_jobs_file("links.txt", data, now=NOW)
    assert first.kind == "links" and first.links == 2
    assert first.dest.parent == service.data_dir / "inbox" / "links"
    assert first.dest != again.dest and first.dest.read_bytes() == data
    paste = service.add_jobs_file("poste.txt", "url: https://x.ca/9\ntitle: Rédacteur\n".encode())
    assert paste.kind == "manual" and paste.dest.parent.name == "manual"
    with pytest.raises(AppError, match="empty"):
        service.add_jobs_file("empty.txt", b"  \n")


# ---- contractor file ----


def test_contractor_file_is_checked_when_picked(service: AppService, tmp_path: Path) -> None:
    path = contractor_file(tmp_path)
    info = service.pick_contractor_file(path.name, path=path)
    assert info.contractors == 1 and service.contractor_file == path

    upload = service.pick_contractor_file("export.csv", data=path.read_bytes())
    assert upload.path.parent == service.data_dir / "contractors" / "uploads"

    with pytest.raises(AppError, match="workbook"):
        service.pick_contractor_file("people.txt", data=b"x")
    with pytest.raises(AppError, match="bad.csv"):
        service.pick_contractor_file("bad.csv", data=b"talent_id,nickname\nTAL-1,x\n")


# ---- settings ----


def test_key_and_reviewer_are_saved_to_the_env_file(
    service: AppService, env: dict[str, str]
) -> None:
    assert not service.has_key() and service.reviewer() == "reviewer"
    service.save_key(f"  {FAKE_KEY}  ")
    service.save_reviewer("  Camille   Roy ")
    assert service.has_key() and service.reviewer() == "Camille Roy"
    saved = read_env_file(service.env_path)
    assert saved == {"GEMINI_API_KEY": FAKE_KEY, REVIEWER_ENV: "Camille Roy"}
    assert env["GEMINI_API_KEY"] == FAKE_KEY
    with pytest.raises(AppError, match="empty"):
        service.save_key("   ")


# ---- run, results, decisions, export ----


def test_run_then_results_in_review_order(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)  # 2 data jobs (C01 fits), 1 recruiting job (nobody fits)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    lines: list[str] = []
    report = service.run(echo=lines.append, confirm=lambda check: False)
    assert report.jobs_matched == 2 and report.jobs_no_match == 1
    assert service.contractor_file is None  # loaded once
    assert any("total" in line for line in lines)  # token line per call

    results = service.results()
    assert [r.outcome for r in results] == ["fits", "fits", "no_match"]
    first = results[0].matches[0]
    assert (first.name, first.match.talent_id, first.decision) == ("Alex Martin", "TAL-1", None)
    assert service.quota().requests == 3


def test_decisions_are_stored_and_the_latest_shows(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: False)
    match_id = service.results()[0].matches[0].match.match_id

    with pytest.raises(AppError, match="reason"):
        service.decide(match_id, Decision.REJECTED)
    service.decide(match_id, Decision.APPROVED)
    service.decide(match_id, Decision.REJECTED, ReasonCode.SENIORITY, "  trop junior ")
    reviews = CsvReviewRepository(store).list_all()
    assert [r.decision for r in reviews] == [Decision.APPROVED, Decision.REJECTED]
    assert reviews[-1].notes == "trop junior" and reviews[-1].reviewer == "reviewer"
    latest = service.results()[0].matches[0].decision
    assert latest is not None and latest.reason_code is ReasonCode.SENIORITY


def test_quota_question_reaches_the_caller(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    assert service.llm is not None
    limits = service.llm.limits.model_copy(update={"rpd": 2})
    service.llm = service.llm.model_copy(update={"limits": limits})
    service.settings = service.settings.model_copy(update={"llm": service.llm})
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    asked: list[RunCheck] = []
    report = service.run(echo=lambda s: None, confirm=lambda c: asked.append(c) or False)
    assert asked and asked[0].verdict == "exceeds" and report.stopped
    assert [r.outcome for r in service.results()] == ["not_judged"] * 3


def test_missing_key_is_a_plain_message(app_settings: Settings, tmp_path: Path) -> None:
    store = CsvStore(app_settings.paths.data_dir, lock_timeout=5)
    job = Job(
        job_id="paste:1", source="paste", source_id="1", url="https://x.ca/1",
        title_raw="Data Analyst", responsibilities=["Build dashboards"], first_seen_at=NOW,
    )  # fmt: skip
    from jobmatch.storage.csv_store import CsvJobRepository

    CsvJobRepository(store).add_new([job])
    real = AppService(app_settings, tmp_path / "app.env", environ={})  # real Gemini, no key
    path = contractor_file(tmp_path)
    real.pick_contractor_file(path.name, path=path)
    with pytest.raises(AppError, match="Gemini key in Settings"):
        real.run(echo=lambda s: None, confirm=lambda c: False)


def test_export_writes_the_spreadsheet(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: False)
    done = service.export()
    assert done.path.exists() and done.rows == 3 and done.jobs == 3
    assert "Alex Martin" in done.path.read_text(encoding="utf-8-sig")


def test_reset_makes_a_match_undecided_and_keeps_history(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    service.run(echo=lambda s: None, confirm=lambda c: False)
    match_id = service.results()[0].matches[0].match.match_id
    service.decide(match_id, Decision.APPROVED)
    service.clear_decision(match_id)
    assert service.results()[0].matches[0].decision is None
    history = [r.decision for r in CsvReviewRepository(store).list_all()]
    assert history == [Decision.APPROVED, Decision.CLEARED]  # append-only record
    service.decide(match_id, Decision.NEEDS_INFO)
    latest = service.results()[0].matches[0].decision
    assert latest is not None and latest.decision is Decision.NEEDS_INFO


def test_profile_fields_are_the_csv_row_in_file_order(service: AppService, tmp_path: Path) -> None:
    from jobmatch.schemas import ContractorProfile
    from jobmatch.sources.contractor_upsert import upsert_contractors

    upsert_contractors(
        [ContractorProfile(
            talent_id="TAL-7", talent_name="Élise Tremblay", rate_expectation="95",
            standardized_headline="Rédactrice UX",
            executive_summary="Écrit des parcours | simples et clairs.",
            core_strengths=["UX writing", "Microcopie"], languages=["fr", "en"],
        )],
        CsvContractorProfileRepository(service.settings.paths.contractors_csv, lock_timeout=5),
    )  # fmt: skip
    fields = dict(service.contractor_fields("TAL-7"))
    columns = [c for c, _ in service.contractor_fields("TAL-7")]
    assert columns[:3] == ["talent_id", "talent_name", "location"]  # the file's own order
    assert fields["talent_name"] == ["Élise Tremblay"]
    assert fields["core_strengths"] == ["UX writing", "Microcopie"]  # list cell split
    assert fields["executive_summary"] == ["Écrit des parcours | simples et clairs."]  # text kept
    assert fields["location"] == []  # empty cell
    with pytest.raises(AppError, match="TAL-404"):
        service.contractor_fields("TAL-404")


def test_progress_moves_forward_to_done(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    seen: list[tuple[float, str]] = []
    service.run(
        echo=lambda s: None, confirm=lambda c: False, progress=lambda f, s: seen.append((f, s))
    )
    shares = [f for f, _ in seen]
    assert shares == sorted(shares) and shares[0] == 0.0 and shares[-1] == 1.0
    stages = [s for _, s in seen]
    assert "Importing jobs" in stages and "Asking Gemini: 3 of 3" in stages
    assert stages[-1] == "Done"
