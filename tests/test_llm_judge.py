"""judge_jobs with fake providers: caching, pacing, daily-limit stop. No network."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import repo_settings
from jobmatch.config import LlmSettings
from jobmatch.llm.anonymize import build_roster
from jobmatch.llm.judge import judge_jobs, load_judgment, plan_jobs
from jobmatch.llm.provider import DailyQuotaError, LlmRequest, LlmResponse, TokenUsage
from jobmatch.llm.usage import UsageLedger
from jobmatch.schemas import ContractorProfile, Job
from jobmatch.storage.csv_store import CsvStore


def llm() -> LlmSettings:
    settings = repo_settings().llm
    assert settings is not None
    return settings


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
        return LlmResponse(text=ANSWER, usage=usage, model="fake", http_status=200, attempts=1)


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
        batch, ROSTER, llm(), fake, UsageLedger(store), folder,
        echo=lines.append, sleep=sleep, clock=clock,
    )  # fmt: skip
    assert (report.sent, report.ok, report.failed, report.cached) == (3, 3, 0, 0)
    assert report.total_tokens == 16_500
    assert len(lines) == 3 and all("total 5,500" in line for line in lines)
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)  # spaced by 60 / RPM
    assert all(p.cached for p in plan_jobs(batch, ROSTER, llm(), folder))

    again = judge_jobs(
        batch, ROSTER, llm(), fake, UsageLedger(store), folder,
        echo=lines.append, sleep=sleep, clock=clock,
    )  # fmt: skip
    assert (again.sent, again.cached) == (0, 3)
    assert fake.calls == 3


def test_daily_limit_stops_the_run_and_keeps_what_was_done(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    folder, clock = tmp_path / "judgments", Clock()
    batch = jobs(make_job, 4)
    report = judge_jobs(
        batch, ROSTER, llm(), Fake(fail_after=2), UsageLedger(store), folder,
        echo=lambda s: None, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert (report.sent, report.ok, report.failed) == (3, 2, 1)
    assert report.stopped is not None and "daily limit" in report.stopped
    plan = plan_jobs(batch, ROSTER, llm(), folder)
    assert [p.cached for p in plan] == [True, True, False, False]
    assert load_judgment(folder, plan[0].key) is not None


def test_max_calls_limits_the_run(
    make_job: Callable[..., Job], store: CsvStore, tmp_path: Path
) -> None:
    clock = Clock()
    report = judge_jobs(
        jobs(make_job, 3), ROSTER, llm(), Fake(), UsageLedger(store), tmp_path / "j",
        max_calls=1, echo=lambda s: None, sleep=clock.sleep, clock=clock,
    )  # fmt: skip
    assert report.sent == 1 and report.stopped == "--max-calls 1 reached"
