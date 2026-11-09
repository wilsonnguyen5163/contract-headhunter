"""judge_jobs with fake providers: caching, pacing, daily-limit stop. No network."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import repo_settings, staged_answer
from jobmatch.config import LlmSettings
from jobmatch.llm.anonymize import build_roster
from jobmatch.llm.judge import judge_jobs, load_judgment, plan_jobs
from jobmatch.llm.provider import (
    DailyQuotaError,
    LlmRequest,
    LlmResponse,
    LlmTimeoutError,
    TokenUsage,
)
from jobmatch.llm.usage import UsageLedger
from jobmatch.schemas import ContractorProfile, Job
from jobmatch.storage.csv_store import CsvStore, CsvUsageRepository


def ledger(store: CsvStore) -> UsageLedger:
    return UsageLedger(CsvUsageRepository(store))


def llm() -> LlmSettings:
    settings = repo_settings().llm
    assert settings is not None
    return settings.model_copy(update={"model_limits": {}})  # one pace for every model


ROSTER = build_roster(
    [
        ContractorProfile(
            talent_id="TAL-1",
            talent_name="Person One",
            standardized_headline="Data Analyst",
            executive_summary="Builds dashboards in Power BI.",
            rate_expectation="Industry Standard",
        )
    ],
    llm().payload,
)
ANSWER = json.dumps(
    {
        "no_fit": False,
        "verdicts": [{"alias": "C01", "verdict": "fit", "reason": "Dashboards."}],
        "top": [
            {
                "alias": "C01",
                "fit_score": 80,
                "requirements_met": [],
                "requirements_missing": [],
                "red_flags": [],
                "evidence": [{"source": "job", "quote": "Build dashboards"}],
            }
        ],
    }
)


class Fake:
    model = "fake"

    def __init__(self, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise DailyQuotaError("daily limit reached", http_status=429, attempts=1)
        usage = TokenUsage(input_tokens=5000, output_tokens=500, total_tokens=5500)
        text = staged_answer(json.loads(ANSWER), request)
        return LlmResponse(text=text, usage=usage, model="fake", http_status=200, attempts=1)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def jobs(make_job: Callable[..., Job], n: int) -> list[Job]:
    return [
        make_job(job_id=f"a:{i}", source_id=str(i), title_norm=f"Data Analyst {i}",
                 responsibilities=["Build dashboards"])
        for i in range(n)
    ]  # fmt: skip


def test_jobs_are_judged_cached_and_paced(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    folder, clock, fake, sleeps = tmp_path / "judgments", Clock(), Fake(), []

    def sleep(s: float) -> None:
        sleeps.append(s)
        clock.sleep(s)

    lines: list[str] = []
    batch = jobs(make_job, 3)
    report = judge_jobs(
        batch, ROSTER, llm(), fake, ledger(store), folder,
        echo=lines.append, sleep=sleep, clock=clock,
    )  # fmt: skip
    # Each job: a screening call, then a review of its shortlist (C01): 2 calls per job.
    assert (report.sent, report.ok, report.failed, report.cached) == (6, 3, 0, 0)
    assert report.reviews == 3
    assert report.total_tokens == 33_000
    assert len(lines) == 6 and all("total 5,500" in line for line in lines)
    assert [line.split()[1] for line in lines] == ["screen", "review"] * 3
    assert sleeps and all(s > 0 for s in sleeps)  # each model spaced by 60 / RPM
    assert all(p.cached for p in plan_jobs(batch, ROSTER, llm(), folder))

    again = judge_jobs(
        batch, ROSTER, llm(), fake, ledger(store), folder,
        echo=lines.append, sleep=sleep, clock=clock,
    )  # fmt: skip
    assert (again.sent, again.cached) == (0, 3)
    assert fake.calls == 6


def test_daily_limit_on_screening_stops_the_run_and_keeps_what_was_done(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    folder, clock = tmp_path / "judgments", Clock()
    batch = jobs(make_job, 4)
    report = judge_jobs(
        batch, ROSTER, llm(), Fake(fail_after=2), ledger(store), folder,
        echo=lambda s: None, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    # Job 1: screen + review; job 2: the screening call hits the daily limit.
    assert (report.sent, report.ok, report.failed) == (3, 1, 1)
    assert report.stopped is not None and "screening model reached its daily limit" in (
        report.stopped
    )
    plan = plan_jobs(batch, ROSTER, llm(), folder)
    assert [p.cached for p in plan] == [True, False, False, False]
    assert load_judgment(folder, plan[0].key) is not None


class ChainFake:
    """One fake per model (with_model), like GeminiProvider. Models in `exhausted` answer
    "daily limit reached"; models in `slow` time out."""

    def __init__(
        self, exhausted: set[str], model: str = "screener", log: list | None = None,
        slow: set[str] | None = None,
    ) -> None:  # fmt: skip
        self.model, self.exhausted = model, exhausted
        self.slow: set[str] = set() if slow is None else slow
        self.log: list[str] = [] if log is None else log

    def with_model(self, model: str) -> "ChainFake":
        return ChainFake(self.exhausted, model, self.log, self.slow)

    def generate(self, request: LlmRequest) -> LlmResponse:
        self.log.append(self.model)
        if self.model in self.exhausted:
            raise DailyQuotaError("daily limit reached", http_status=429, attempts=1)
        if self.model in self.slow:
            raise LlmTimeoutError("no answer within 120 s", attempts=1)
        usage = TokenUsage(input_tokens=5000, output_tokens=500, total_tokens=5500)
        text = staged_answer(json.loads(ANSWER), request)
        return LlmResponse(text=text, usage=usage, model=self.model, http_status=200, attempts=1)


def test_review_falls_back_down_the_chain_when_a_model_is_used_up(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    """User design 2026-11-08: the strongest model with quota left reviews; a model that
    reached its daily limit is skipped for the rest of the run."""
    settings = llm()
    first, second = settings.review_chain[:2]
    fake, clock, lines = ChainFake({first}), Clock(), []
    folder = tmp_path / "judgments"
    batch = jobs(make_job, 2)
    report = judge_jobs(
        batch, ROSTER, settings, fake, ledger(store), folder,
        echo=lines.append, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert (report.ok, report.failed, report.reviews) == (2, 0, 2)
    assert report.exhausted == [first]
    # Job 1 tries the first model once, then the second; job 2 goes straight to the second.
    assert fake.log == [settings.model, first, second, settings.model, second]
    assert any(f"{first} reached its daily limit" in line for line in lines)
    result = load_judgment(folder, plan_jobs(batch, ROSTER, settings, folder)[0].key)
    assert result is not None and result.review_model == second and result.model == second


def test_a_review_model_that_times_out_is_skipped_for_the_run(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    """User decision 2026-11-08: a timeout moves on to the next model in the chain."""
    settings = llm()
    first, second = settings.review_chain[:2]
    fake, clock, lines = ChainFake(set(), slow={first}), Clock(), []
    report = judge_jobs(
        jobs(make_job, 2), ROSTER, settings, fake, ledger(store), tmp_path / "j",
        echo=lines.append, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert (report.ok, report.reviews, report.timed_out) == (2, 2, [first])
    assert fake.log == [settings.model, first, second, settings.model, second]
    assert any(f"{first} did not answer in time" in line for line in lines)
    assert any("· timeout ·" in line for line in lines)  # logged as a timeout


def test_a_failed_review_is_retried_without_screening_again(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    settings = llm()
    every_reviewer = set(settings.review_chain) - {settings.model}
    fake, clock = ChainFake(every_reviewer | {settings.model}), Clock()
    # Screening works only when the screener is not exhausted: let it pass once.
    fake.exhausted.discard(settings.model)
    folder, batch = tmp_path / "judgments", jobs(make_job, 1)
    first = judge_jobs(
        batch, ROSTER, settings.model_copy(update={"review_models": sorted(every_reviewer)}),
        fake, ledger(store), folder, echo=lambda s: None, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert first.stopped is not None and "every review model" in first.stopped
    assert first.ok == 0 and not plan_jobs(batch, ROSTER, settings, folder)[0].cached
    fake.exhausted.clear()  # the next day
    calls_before = len(fake.log)
    again = judge_jobs(
        batch, ROSTER, settings.model_copy(update={"review_models": sorted(every_reviewer)}),
        fake, ledger(store), folder, echo=lambda s: None, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert again.ok == 1
    assert settings.model not in fake.log[calls_before:]  # the screening was kept


def test_nobody_shortlisted_means_no_review_call(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    class NoFit(Fake):
        def generate(self, request: LlmRequest) -> LlmResponse:
            self.calls += 1
            answer = {
                "no_fit": True,
                "verdicts": [{"alias": "C01", "verdict": "no", "reason": "Recruteur."}],
                "top": [],
            }
            usage = TokenUsage(input_tokens=5000, output_tokens=200, total_tokens=5200)
            text = staged_answer(answer, request)
            return LlmResponse(text=text, usage=usage, model="fake", http_status=200, attempts=1)

    fake, clock = NoFit(), Clock()
    report = judge_jobs(
        jobs(make_job, 2), ROSTER, llm(), fake, ledger(store), tmp_path / "j",
        echo=lambda s: None, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert (report.ok, report.sent, report.reviews, fake.calls) == (2, 2, 0, 2)


def test_max_calls_limits_the_run(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    clock = Clock()
    report = judge_jobs(
        jobs(make_job, 3), ROSTER, llm(), Fake(), ledger(store), tmp_path / "j",
        max_calls=1, echo=lambda s: None, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    # --max-calls caps the jobs started: one job = its screening plus its review.
    assert report.sent == 2 and report.stopped == "--max-calls 1 reached"
