"""The Jobs screen: job states, the matching summary, the Completed / Unavailable / Reopen
toggles, and the jobs.csv columns they need. Fake Gemini, no network. English and French
postings (test_pipeline.add_jobs)."""

import asyncio
import csv
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation

from jobmatch.app.service import AppError, AppService, job_summary
from jobmatch.app.ui import build_root, keep_order
from jobmatch.schemas import Job, JobAvailability, LlmMatch, LlmVerdict
from jobmatch.storage.csv_store import CsvJobRepository, CsvStore
from test_pipeline import add_jobs, contractor_file

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _match(verdict: LlmVerdict, score: int | None, rank: int = 1) -> LlmMatch:
    return LlmMatch(
        match_id=f"m{rank}", job_id="paste:1", talent_id=f"TAL-{rank}", rank=rank,
        verdict=verdict, fit_score=score, model="fake", prompt_version="v", judge_key="k",
        created_at=NOW,
    )  # fmt: skip


def _states(service: AppService) -> dict[str, str]:
    return {o.job.job_id: o.state for o in service.jobs_overview()}


def _run(service: AppService) -> None:
    service.run(echo=lambda s: None, confirm=lambda c: False)


@pytest.fixture
def judged(service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path):
    """Three jobs (2 French) judged once: two matched, the recruiting one with no match."""
    add_jobs(store, make_job)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    _run(service)
    return service


# ---- the summary line ----


def test_summary_texts(make_job: Callable[..., Job]) -> None:
    job = make_job()
    fit = LlmVerdict.FIT
    three = [_match(fit, 92, 1), _match(fit, 80, 2), _match(fit, 71, 3)]
    assert job_summary(job, three) == "3 matches · best score 92"
    assert job_summary(job, [_match(fit, 75)]) == "1 match · best score 75"
    assert job_summary(job, [_match(LlmVerdict.PARTIAL, 58)]) == "best score 58"
    assert job_summary(job, []) == "no match"
    assert job_summary(job, None) == ""  # the state label says "Not scanned yet"
    done = make_job(availability=JobAvailability.COMPLETED)
    assert job_summary(done, three) == "completed"
    assert job_summary(make_job(availability="unavailable"), None) == "unavailable"


# ---- states ----


def test_new_jobs_wait_then_get_their_state(
    service: AppService, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    path = contractor_file(tmp_path)
    service.pick_contractor_file(path.name, path=path)
    assert set(_states(service).values()) == {"waiting"}  # state 3: not analyzed yet
    _run(service)
    overview = service.jobs_overview()
    # A–Z by title, French titles included.
    assert [o.job.title_norm for o in overview] == [
        "Analyste de données", "Data Analyst (Contract)", "Recruteur (contrat)",
    ]  # fmt: skip
    assert [o.state for o in overview] == ["matched", "matched", "no_match"]
    assert overview[0].summary.startswith("1 match · best score ")
    assert overview[2].summary == "no match"


def test_completed_job_leaves_results_and_is_never_sent(judged: AppService) -> None:
    service = judged
    service.set_job_availability("paste:1", "completed", now=NOW)
    service.set_job_availability("paste:3", "unavailable", now=NOW)
    assert _states(service) == {"paste:1": "inactive", "paste:2": "matched", "paste:3": "inactive"}
    assert [r.job.job_id for r in service.results()] == ["paste:2"]
    job = CsvJobRepository(CsvStore(service.data_dir, lock_timeout=5)).get("paste:1")
    assert job is not None and job.availability is JobAvailability.COMPLETED
    assert job.availability_changed_at == NOW
    fake = service._provider_factory()  # type: ignore[misc]
    before = fake.calls
    _run(service)
    assert fake.calls == before  # nothing new to send: closed jobs are skipped


def test_reopen_scans_only_contractors_not_judged_yet(judged: AppService) -> None:
    service = judged
    service.set_job_availability("paste:3", "unavailable", now=NOW)
    service.set_job_availability("paste:3", "reopen", now=NOW)
    # Everyone was judged for it already (0.4, user decision A): nothing to send, and the
    # job is back with its result. New contractors would be sent (tests/test_rounds.py).
    assert _states(service)["paste:3"] == "no_match"
    fake = service._provider_factory()  # type: ignore[misc]
    before = fake.calls
    _run(service)
    assert fake.calls == before


def test_closed_jobs_go_to_the_bottom_a_to_z(judged: AppService) -> None:
    judged.set_job_availability("paste:2", "unavailable", now=NOW)  # Analyste de données
    judged.set_job_availability("paste:1", "completed", now=NOW)  # Data Analyst
    assert [o.job.job_id for o in judged.jobs_overview()] == ["paste:3", "paste:2", "paste:1"]


def test_keep_order_holds_cards_in_place(judged: AppService) -> None:
    on_screen = [o.job.job_id for o in judged.jobs_overview()]
    judged.set_job_availability("paste:2", "unavailable", now=NOW)
    kept = keep_order(judged.jobs_overview(), on_screen)
    assert [o.job.job_id for o in kept] == on_screen
    assert kept[0].state == "inactive"  # repainted, not moved


def test_toggles_only_where_they_make_sense(judged: AppService) -> None:
    with pytest.raises(AppError, match="Only a completed or unavailable job"):
        judged.set_job_availability("paste:1", "reopen")
    judged.set_job_availability("paste:1", "completed")
    with pytest.raises(AppError, match="already marked"):
        judged.set_job_availability("paste:1", "unavailable")
    with pytest.raises(AppError, match="not in the job list"):
        judged.set_job_availability("nope", "completed")


# ---- jobs.csv written before these columns ----


def test_older_jobs_csv_loads_and_is_upgraded_with_a_backup(
    store: CsvStore, make_job: Callable[..., Job]
) -> None:
    repo = CsvJobRepository(store)
    repo.add_new([make_job(job_id="paste:1", source_id="1")])
    new_columns = ["availability", "availability_changed_at", "reopened_at"]
    # Rewrite the file as 0.3 wrote it: no availability columns.
    with store.jobs.path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    keep = [i for i, name in enumerate(rows[0]) if name not in new_columns]
    with store.jobs.path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows([[r[i] for i in keep] for r in rows])
    sidecar = json.loads(store.jobs.sidecar_path.read_text(encoding="utf-8"))
    sidecar["columns"] = [c for c in sidecar["columns"] if c not in new_columns]  # as 0.3 wrote
    store.jobs.sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    [old] = repo.list_all()
    assert old.availability is JobAvailability.AVAILABLE  # default for the missing column
    repo.add_new([make_job(job_id="paste:2", source_id="2", title_raw="Analyste (mandat)")])
    assert [j.job_id for j in repo.list_all()] == ["paste:1", "paste:2"]
    with store.jobs.path.open(encoding="utf-8", newline="") as f:
        assert next(csv.reader(f))[-3:] == new_columns
    assert list(store.backups_dir.glob("jobs.*.csv"))  # the old file was kept
    sidecar = json.loads(store.jobs.sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["columns"][-3:] == new_columns


# ---- the screen ----


def test_jobs_screen_colours_and_toggles(judged: AppService) -> None:
    service = judged

    async def scenario(user) -> None:  # noqa: ANN001
        await user.open("/jobs")
        await user.should_see("Analyste de Données")
        await user.should_see("Scanned · 1 match · best score", marker="job-summary")
        await user.should_see("Scanned · no match", marker="job-summary")
        await user.should_see("3 job(s) · 3 scanned (2 with matches, 1 with no match)")
        red = next(iter(user.find(marker="job-state-no_match").elements))
        assert "background-color: #ff7d7d" in _css(red)
        await user.should_not_see(marker="reopen:paste:3")  # Reopen: closed jobs only
        user.find(marker="unavailable:paste:3").click()  # the French recruiting job
        await user.should_see("Closed · unavailable", marker="job-summary")
        await user.should_see(marker="reopen:paste:3")
        grey = next(iter(user.find(marker="job-state-inactive").elements))
        assert "background-color: #EEEEEE" in _css(grey)
        # The first card (Analyste de données) is closed: it stays first until the page reloads.
        user.find(marker="completed:paste:2").click()
        await user.should_see(marker="reopen:paste:2")
        assert _lines(user)[0] == "Closed · completed"
        await user.open("/jobs")
        assert _lines(user)[-2:] == ["Closed · completed", "Closed · unavailable"]  # A–Z

    async def main() -> None:
        async with user_simulation(root=build_root(service, native=False)) as user:
            await scenario(user)

    asyncio.run(main())
    assert _states(service)["paste:3"] == _states(service)["paste:2"] == "inactive"


def _lines(user) -> list[str]:  # noqa: ANN001
    """The cards' summary lines, top to bottom (elements are created in screen order)."""
    labels = sorted(user.find(marker="job-summary").elements, key=lambda e: e.id)
    return [label.text for label in labels]


def _css(element: ui.element) -> str:
    return "; ".join(f"{k}: {v}" for k, v in element.style.items())
