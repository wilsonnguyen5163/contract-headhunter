"""Hires and deletions (user decisions 2026-11-10):
- "Hired" makes the contractor not available; undoing the hire restores what it was before,
  unless a reviewer changed it since.
- A closed job can be deleted; its approved contractors still see it on the Talent screen.
- A not-available contractor can be deleted with everything about them; a later import adds
  them back. Every rewritten CSV is backed up first. Fake Gemini, no network."""

from pathlib import Path

import pytest

from jobmatch.app.service import AppError, AppService
from jobmatch.schemas import ApplicationStatus, Decision
from jobmatch.sources.resumes import resumes_dir
from test_app_ui import run_ui
from test_resumes import pdf
from test_talent import MarkerGemini, match_of, picked, profile, talent  # noqa: F401 (fixture)


def approve(service: AppService, talent_id: str) -> None:
    service.decide(match_of(service, talent_id), Decision.APPROVED)


def available(service: AppService) -> dict[str, bool]:
    return {t.talent_id: t.available for t in service.talent_overview()}


def test_hire_and_undo(talent) -> None:  # noqa: ANN001, F811
    service, _ = talent
    approve(service, "TAL-1")
    service.set_application("paste:1", "TAL-1", ApplicationStatus.HIRED)
    assert not available(service)["TAL-1"]
    # The reviewer switches them back on themselves: undoing the hire leaves that alone.
    service.set_talent_availability("TAL-1", True, "Free again next month")
    service.set_application("paste:1", "TAL-1", ApplicationStatus.IN_PROGRESS)
    rows = [t for t in service._loader().talent_availability.list_all() if t.talent_id == "TAL-1"]
    assert [r.reviewer for r in rows] == ["hire", "reviewer"]


def test_delete_closed_job_keeps_it_on_talent(talent) -> None:  # noqa: ANN001, F811
    service, _ = talent
    approve(service, "TAL-2")
    service.set_application("paste:1", "TAL-2", ApplicationStatus.OUTREACHED)
    with pytest.raises(AppError, match="Only a completed or unavailable job"):
        service.delete_job("paste:1")
    service.set_job_availability("paste:1", "unavailable")
    assert service.delete_job("paste:1") == 1
    loader = service._loader()
    assert loader.jobs.get("paste:1") is None
    assert [r for r in loader.job_scans.list_all() if r.job_id == "paste:1"] == []
    assert [a for a in loader.applications.list_all() if a.job_id == "paste:1"] == []
    assert service.jobs_overview() == [] and service.results() == []
    [job] = {t.talent_id: t for t in service.talent_overview()}["TAL-2"].jobs
    assert (job.title, job.company, job.status, job.deleted, job.job_open) == (
        "Analyste de données (contrat)", "Ville de Québec", ApplicationStatus.OUTREACHED,
        True, False,
    )  # fmt: skip
    backups = service.data_dir / "backups"
    assert any(p.name.startswith("jobs.") for p in backups.iterdir())  # backed up first


def test_delete_talent_removes_everything(talent, tmp_path: Path) -> None:  # noqa: ANN001, F811
    service, fake = talent
    approve(service, "TAL-3")
    service.set_application("paste:1", "TAL-3", ApplicationStatus.IN_PROGRESS)
    folder = resumes_dir(service.data_dir)
    pdf(folder / "TAL-3.pdf")
    pdf(folder / "old" / "TAL-3-20261101T120000Z.pdf")
    pdf(folder / "old" / "TAL-30-20261101T120000Z.pdf")  # someone else: kept
    with pytest.raises(AppError, match="not available"):
        service.delete_talent("TAL-3")
    service.set_talent_availability("TAL-3", False, "Moved abroad")

    service.delete_talent("TAL-3")
    loader = service._loader()
    assert "TAL-3" not in {p.talent_id for p in loader.contractors.list_all()}
    for repo in (
        loader.llm_matches,
        loader.job_scans,
        loader.job_rounds,
        loader.applications,
        loader.talent_availability,
        loader.contractor_changes,
        loader.removed_jobs,
    ):
        assert all(r.talent_id != "TAL-3" for r in repo.list_all()), repo
    assert not (folder / "TAL-3.pdf").exists()
    assert [p.name for p in (folder / "old").iterdir()] == ["TAL-30-20261101T120000Z.pdf"]
    matches = {m.match_id for m in loader.llm_matches.list_all()}
    assert all(r.match_id in matches for r in loader.reviews.list_all())  # their decisions too
    assert "TAL-3" not in available(service)

    # The contractor file still has them: importing it again adds them back, as new.
    path = picked(tmp_path, [profile(3)])
    service.pick_contractor_file(path.name, path=path)
    report = service.run(echo=lambda s: None, confirm=lambda c: True)
    assert report.contractors is not None and report.contractors.added == ["TAL-3"]
    assert fake.screened[-1] == [85]  # matched again, alone


def test_delete_buttons_ask_first(talent) -> None:  # noqa: ANN001, F811
    service, _ = talent
    service.set_talent_availability("TAL-4", False)

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/talent")
        await user.should_not_see(marker="delete-talent:TAL-1")  # available: no button
        await user.should_see(marker="delete-talent:TAL-5")  # profile says not open
        user.find(marker="delete-talent:TAL-4").click()
        await user.should_see("Delete Person 4?")
        user.find(marker="delete-cancel").click()
        await user.should_see(marker="delete-talent:TAL-4")
        user.find(marker="delete-talent:TAL-4").click()
        user.find(marker="delete-confirm").click()
        await user.should_not_see(marker="delete-talent:TAL-4")

    run_ui(service, scenario)
    assert "TAL-4" not in available(service)
