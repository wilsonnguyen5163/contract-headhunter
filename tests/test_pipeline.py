"""run_once end to end with a fake Gemini and no fetching: contractors upserted, jobs judged
once, shortlists stored, statuses set, budget questions honoured, runs never overlap."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock

from conftest import staged_answer
from jobmatch.config import Settings
from jobmatch.llm.provider import LlmRequest, LlmResponse, TokenUsage
from jobmatch.llm.usage import RunCheck
from jobmatch.pipeline import (
    PIPELINE_LOCK_FILE,
    PipelineError,
    RunOnceReport,
    run_once,
)
from jobmatch.schemas import ContractorProfile, Job, JobStatus
from jobmatch.shortlists import sync_rounds
from jobmatch.sources.contractor_upsert import upsert_contractors
from jobmatch.storage.csv_store import (
    CsvContractorProfileRepository,
    CsvJobRepository,
    CsvRunRepository,
    CsvStore,
)
from jobmatch.storage.loader import create_loader


class FakeGemini:
    """Says C01 fits every job except the recruiting one. Counts calls."""

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if "Recruteur" in request.user:
            answer = {
                "no_fit": True,
                "verdicts": [{"alias": "C01", "verdict": "no", "reason": "Pas recruteur."}],
                "top": [],
            }
        else:
            answer = {
                "no_fit": False,
                "verdicts": [{"alias": "C01", "verdict": "fit", "reason": "Dashboards."}],
                "top": [{
                    "alias": "C01", "fit_score": 84, "requirements_met": ["dashboards"],
                    "requirements_missing": [], "red_flags": [],
                    "evidence": [{"source": "job", "quote": "Build dashboards"}],
                }],
            }  # fmt: skip
        usage = TokenUsage(input_tokens=4000, output_tokens=400, total_tokens=4400)
        return LlmResponse(
            text=staged_answer(answer, request), usage=usage, model="fake", http_status=200,
            attempts=1,
        )  # fmt: skip


@pytest.fixture
def app_settings(settings: Settings, data_dir: Path) -> Settings:
    """Repo settings pointed at a temp data folder; fast pacing so tests do not sleep."""
    assert settings.llm is not None
    paths = settings.paths.model_copy(
        update={
            "data_dir": data_dir,
            "exports_dir": data_dir.parent / "exports",
            "contractors_csv": data_dir / "contractors" / "contractors.csv",
        }
    )
    limits = settings.llm.limits.model_copy(update={"rpm": 60_000, "rpd": 500})
    llm = settings.llm.model_copy(update={"limits": limits})
    return settings.model_copy(update={"paths": paths, "llm": llm})


def with_rpd(settings: Settings, rpd: int) -> Settings:
    assert settings.llm is not None
    limits = settings.llm.limits.model_copy(update={"rpd": rpd})
    return settings.model_copy(update={"llm": settings.llm.model_copy(update={"limits": limits})})


def contractor_file(tmp_path: Path) -> Path:
    path = tmp_path / "picked" / "contractors-export.csv"
    upsert_contractors(
        [ContractorProfile(
            talent_id="TAL-1", talent_name="Alex Martin", rate_expectation="90",
            standardized_headline="Data Analyst",
            executive_summary="Builds dashboards in Power BI for public agencies.",
        )],
        CsvContractorProfileRepository(path, lock_timeout=5),
    )  # fmt: skip
    return path


def add_jobs(store: CsvStore, make_job: Callable[..., Job]) -> None:
    CsvJobRepository(store).add_new([
        make_job(job_id="paste:1", source_id="1", title_norm="Data Analyst (Contract)",
                 responsibilities=["Build dashboards in Power BI"]),
        make_job(job_id="paste:2", source_id="2", title_norm="Analyste de données",
                 responsibilities=["Build dashboards pour les ministères"], language="fr"),
        make_job(job_id="paste:3", source_id="3", title_norm="Recruteur (contrat)",
                 responsibilities=["Recruter des profils TI"], language="fr"),
    ])  # fmt: skip


def go(settings: Settings, fake: FakeGemini, **kwargs: Any) -> RunOnceReport:
    return run_once(
        settings, provider_factory=lambda: fake, link_paths=[], paste_paths=[],
        echo=lambda line: None, **kwargs,
    )  # fmt: skip


def test_full_run_then_rerun_costs_nothing(
    app_settings: Settings, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    fake = FakeGemini()
    report = go(app_settings, fake, contractor_file=contractor_file(tmp_path))
    assert report.contractors is not None and report.contractors.added == ["TAL-1"]
    # 3 screenings, then the 2 jobs with a fit reviewed together in one call (v15 batch of 2);
    # the recruiting job has nobody shortlisted, so no review.
    assert fake.calls == 4
    assert (report.jobs_matched, report.jobs_no_match, report.matches_added) == (2, 1, 2)
    statuses = {j.job_id: j.status for j in CsvJobRepository(store).list_all()}
    assert statuses == {
        "paste:1": JobStatus.MATCHED, "paste:2": JobStatus.MATCHED,
        "paste:3": JobStatus.NO_MATCH,
    }  # fmt: skip
    pictures = sync_rounds(app_settings, create_loader(app_settings))
    assert {k: [m.talent_id for m in p.shortlist.shown] for k, p in pictures.items()} == {
        "paste:1": ["TAL-1"], "paste:2": ["TAL-1"], "paste:3": [],
    }  # fmt: skip
    # Each job records who was judged for it, so the next run sends nobody.
    scanned = {(s.job_id, s.talent_id) for s in create_loader(app_settings).job_scans.list_all()}
    assert scanned == {("paste:1", "TAL-1"), ("paste:2", "TAL-1"), ("paste:3", "TAL-1")}

    again = go(app_settings, fake)
    assert fake.calls == 4 and again.judge is None
    assert again.matches_added == 0
    runs = CsvRunRepository(store).list_all()
    assert [r.run_id.startswith("run-") for r in runs[-2:]] == [True, True]
    assert app_settings.llm is not None
    assert runs[-2].matched == 2 and runs[-2].model_name == app_settings.llm.model


def test_no_api_call_without_a_yes_when_the_quota_is_short(
    app_settings: Settings, store: CsvStore, make_job: Callable[..., Job], tmp_path: Path
) -> None:
    add_jobs(store, make_job)
    tight = with_rpd(app_settings, 2)  # 3 calls needed, 2 requests a day
    asked: list[RunCheck] = []
    fake = FakeGemini()

    def say_no(check: RunCheck) -> bool:
        asked.append(check)
        return False

    report = go(tight, fake, contractor_file=contractor_file(tmp_path), confirm=say_no)
    assert fake.calls == 0 and asked[0].verdict == "exceeds"
    assert report.stopped and report.jobs_not_judged == 3
    assert all(j.status is JobStatus.NEW for j in CsvJobRepository(store).list_all())

    yes = go(tight, fake, confirm=lambda check: True)
    # Only 1 job fits under 80% of the 2 screening requests left today: its screening and
    # its review (the review uses another model's quota).
    assert fake.calls == 2
    assert yes.jobs_not_judged == 2


def test_needs_a_contractor_list(app_settings: Settings) -> None:
    with pytest.raises(PipelineError, match="contractor"):
        go(app_settings, FakeGemini())


def test_two_runs_never_overlap(app_settings: Settings, data_dir: Path, tmp_path: Path) -> None:
    timeout = app_settings.storage.model_copy(update={"lock_timeout_seconds": 0.1})
    quick = app_settings.model_copy(update={"storage": timeout})
    # filelock on Linux locks per open file, so a second lock object waits like a 2nd run
    with (
        FileLock(str(data_dir / PIPELINE_LOCK_FILE)),
        pytest.raises(PipelineError, match="another run"),
    ):
        go(quick, FakeGemini(), contractor_file=contractor_file(tmp_path))
