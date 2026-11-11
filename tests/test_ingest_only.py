"""The "Ingest" button (user request 2026-11-10): store the picked contractors (with résumés)
and jobs without matching, so the reviewer can check them before Find matches. Fake Gemini,
no network."""

from pathlib import Path

from jobmatch.app.service import AppService
from jobmatch.schemas import RunStatus
from jobmatch.storage.csv_store import CsvRunRepository, CsvStore
from test_app_ui import run_ui
from test_ingest import BROKEN, EN_CONTRACT, FR_FULL_SAME_URL
from test_resumes import folder
from test_talent import MarkerGemini


def make_service(app_settings, tmp_path: Path) -> tuple[AppService, MarkerGemini]:  # noqa: ANN001
    fake = MarkerGemini()
    service = AppService(
        app_settings, tmp_path / "app.env", provider_factory=lambda: fake, environ={}
    )
    return service, fake


def test_ingest_stores_without_matching(app_settings, store: CsvStore, tmp_path: Path) -> None:  # noqa: ANN001
    service, fake = make_service(app_settings, tmp_path)
    service.pick_contractor_folder(folder(tmp_path))
    service.add_jobs_file("analyst.txt", EN_CONTRACT.encode())
    service.add_jobs_file("gestionnaire.txt", FR_FULL_SAME_URL.encode())

    report = service.run(echo=lambda s: None, confirm=lambda c: True, match=False)
    assert report.ingest_only and fake.calls == 0  # nothing sent to Gemini
    assert report.contractors is not None and len(report.contractors.added) == 2
    assert report.resumes is not None and report.resumes.added == 2
    assert sum(i.count("new") for i in report.ingest) == 2
    assert service.contractor_count() == 2
    assert service.contractor_file is None and service.resumes == {}  # picks are cleared
    assert {r.outcome for r in service.results()} == {"not_judged"}  # listed, not judged
    [row] = [r for r in CsvRunRepository(store).list_all() if r.run_id == report.run_id]
    assert "ingest_only=1" in row.source_counts and row.status is RunStatus.OK

    # Find matches afterwards: the stored jobs and contractors are matched.
    report = service.run(echo=lambda s: None, confirm=lambda c: True)
    assert not report.ingest_only and fake.calls > 0
    assert report.contractors is None  # nothing picked again: nothing re-imported


def test_ingest_jobs_before_any_contractor(app_settings, tmp_path: Path) -> None:  # noqa: ANN001
    service, fake = make_service(app_settings, tmp_path)
    service.add_jobs_file("analyst.txt", EN_CONTRACT.encode())
    report = service.run(echo=lambda s: None, confirm=lambda c: True, match=False)
    assert sum(i.count("new") for i in report.ingest) == 1 and fake.calls == 0


def test_find_matches_page(app_settings, tmp_path: Path) -> None:  # noqa: ANN001
    service, fake = make_service(app_settings, tmp_path)
    service.pick_contractor_folder(folder(tmp_path))
    service.run(echo=lambda s: None, confirm=lambda c: True, match=False)

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/")
        await user.should_see("Contractors (optional)")
        await user.should_not_see("1. Contractors")
        await user.should_see("2 contractor(s) processed.")
        await user.should_not_see("Repick the contractor folder")
        await user.should_see("A jobs CSV (one row per posting, from the template)")
        await user.should_see("Nothing picked yet", marker="contractor-folder-path")
        await user.should_see("Choose folder…", marker="contractor-folder")
        await user.should_see("Choose files…", marker="jobs-files")
        await user.should_see("Nothing picked yet", marker="jobs-files-path")
        await user.should_see(marker="ingest")
        await user.should_see(marker="find-matches")
        user.find(marker="ingest").click()
        await user.should_see("Ingested: ", retries=40)
        await user.should_see("Nothing sent to Gemini.")

    run_ui(service, scenario)
    assert fake.calls == 0


def test_stored_files_leave_the_inbox(app_settings, tmp_path: Path) -> None:  # noqa: ANN001
    """User request 2026-11-10: a second Ingest reports only what is new, not past files."""
    service, _ = make_service(app_settings, tmp_path)
    inbox = service.data_dir / "inbox" / "manual"
    lines: list[str] = []

    service.add_jobs_file("analyst.txt", EN_CONTRACT.encode())
    first = service.run(echo=lines.append, confirm=lambda c: True, match=False)
    assert [o.outcome for i in first.ingest for o in i.outcomes] == ["new"]
    assert not list(inbox.glob("*.txt")) and len(list((inbox / "done").glob("*.txt"))) == 1

    service.add_jobs_file("gestionnaire.txt", FR_FULL_SAME_URL.encode())
    service.add_jobs_file("broken.txt", BROKEN.encode())
    second = service.run(echo=lines.append, confirm=lambda c: True, match=False)
    assert sorted(o.outcome for i in second.ingest for o in i.outcomes) == ["error", "new"]
    assert [p.name.split("-", 1)[1] for p in inbox.glob("*.txt")] == ["broken.txt"]  # kept
    assert "  1 file(s) with errors stay in the inbox for the next run" in lines

    third = service.run(echo=lambda s: None, confirm=lambda c: True, match=False)
    assert [o.outcome for i in third.ingest for o in i.outcomes] == ["error"]  # only the retry
