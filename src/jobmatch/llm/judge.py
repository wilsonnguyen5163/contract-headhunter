"""Judge many jobs with every guard: two calls per job (v12, user design 2026-11-08).

1. Screening (llm.model): every contractor, one-line verdicts, a shortlist of those who fit
   or partly fit (up to top_k; empty when nobody fits, and then there is no second call).
2. Deep review of the shortlist only, by the first model in llm.review_models that still has
   quota today. When Google answers "daily limit reached" for a model, or the model does not
   answer within llm.timeout_seconds, it is skipped for the rest of the run and the next model
   in the list reviews instead (the next run tries it first again).

- Each job's joined answer is cached under data/llm_results/judgments/<judge_key>.json; the
  key covers the screening model, prompt version, thinking setting, job text and roster, so
  a re-run only pays for what changed. The screening answer is also kept on its own
  (judgments/screening/), so a review that failed is retried without screening again.
- Calls are paced per model to stay under its RPM and TPM (usage.Pacer).
- The run stops when the screening model, or every review model, reaches its daily limit;
  the next run picks up where it stopped.
- Nothing here changes jobs.csv or any match file.
"""

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from jobmatch.config import LlmSettings
from jobmatch.llm.anonymize import Roster
from jobmatch.llm.payload import job_card, render_job
from jobmatch.llm.prompt import PreparedCall, ReviewOutput, prepare_call, prepare_review
from jobmatch.llm.provider import LLMProvider
from jobmatch.llm.runner import CallOutcome, send
from jobmatch.llm.trial import Screened, TrialResult, join_answers, parse_review, parse_screen
from jobmatch.llm.usage import Pacer, UsageLedger
from jobmatch.schemas import Job, LlmCallStatus, LlmUsage
from jobmatch.storage.files import atomic_write_text

JUDGMENTS_DIR = Path("llm_results") / "judgments"  # under data_dir
SCREENING_DIR = "screening"  # under the judgments folder


def judgments_dir(data_dir: Path) -> Path:
    return data_dir / JUDGMENTS_DIR


def load_judgment(folder: Path, key: str) -> TrialResult | None:
    """A cached answer that parsed; None when missing or unusable (it is asked again)."""
    path = folder / f"{key}.json"
    if not path.exists():
        return None
    try:
        result = TrialResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, ValidationError):
        return None
    if result.judged is None or result.judged.output is None:
        return None
    return result


def save_judgment(folder: Path, result: TrialResult) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{result.judge_key}.json"
    atomic_write_text(path, result.model_dump_json(indent=2) + "\n")
    return path


class ScreeningRecord(BaseModel):
    """A screening answer kept on its own, so a failed review never pays for it twice."""

    job_id: str
    model: str
    judge_key: str
    usage: LlmUsage
    screened: Screened
    raw_text: str | None


def _load_screening(folder: Path, key: str) -> ScreeningRecord | None:
    path = folder / SCREENING_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        record = ScreeningRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, ValidationError):
        return None
    return record if record.screened.output is not None else None


def _save_screening(folder: Path, record: ScreeningRecord) -> None:
    target = folder / SCREENING_DIR
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target / f"{record.judge_key}.json", record.model_dump_json(indent=2) + "\n")


class PlannedJob(BaseModel):
    job_id: str
    call: PreparedCall  # the screening call
    key: str
    cached: bool


def plan_jobs(
    jobs: Sequence[Job], roster: Roster, settings: LlmSettings, folder: Path
) -> list[PlannedJob]:
    planned: list[PlannedJob] = []
    for job in jobs:
        call = prepare_call(job_card(job, settings.payload), roster, settings)
        key = call.judge_key(settings.model)
        cached = load_judgment(folder, key) is not None
        planned.append(PlannedJob(job_id=job.job_id, call=call, key=key, cached=cached))
    return planned


class JudgeRunReport(BaseModel):
    planned: int
    cached: int
    sent: int  # calls sent (screening and review)
    ok: int  # jobs with a usable answer
    failed: int  # jobs without one
    reviews: int = 0  # deep-review calls that answered
    stopped: str | None = None  # why the run stopped early
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    exhausted: list[str] = []  # models that reached their daily limit during the run
    timed_out: list[str] = []  # review models that did not answer in time (skipped after)


class Chain:
    """Per-run state for the model chain: one provider and pacer per model, and the models
    that reached their daily limit."""

    def __init__(
        self,
        settings: LlmSettings,
        provider: LLMProvider,
        ledger: UsageLedger,
        report: JudgeRunReport,
        *,
        echo: Callable[[str], None],
        sleep: Callable[[float], None],
        clock: Callable[[], datetime],
    ) -> None:
        self.settings = settings
        self._provider = provider
        self._providers: dict[str, LLMProvider] = {}
        self._pacers: dict[str, Pacer] = {}
        self.ledger = ledger
        self.report = report
        self.echo = echo
        self.sleep = sleep
        self.clock = clock

    def provider(self, model: str) -> LLMProvider:
        if model not in self._providers:
            with_model = getattr(self._provider, "with_model", None)
            self._providers[model] = with_model(model) if with_model else self._provider
        return self._providers[model]

    def call(self, model: str, call: PreparedCall, purpose: str) -> CallOutcome:
        settings = self.settings.for_model(model)
        pacer = self._pacers.setdefault(model, Pacer(settings.limits))
        wait = pacer.wait_seconds(self.clock(), call.estimated_total_tokens)
        if wait > 0:
            self.sleep(wait)
        outcome = send(
            self.provider(model), self.ledger, settings, call.request,
            purpose=purpose, job_ids=call.job_ids,
            estimated_tokens=call.estimated_total_tokens, clock=self.clock,
        )  # fmt: skip
        self.echo(outcome.line)
        if outcome.usage.attempts:
            self.report.sent += 1
            pacer.record(self.clock(), outcome.usage.total_tokens or call.estimated_total_tokens)
        self.report.input_tokens += outcome.usage.input_tokens
        self.report.output_tokens += outcome.usage.output_tokens
        self.report.total_tokens += outcome.usage.total_tokens
        if outcome.usage.status is LlmCallStatus.DAILY_QUOTA and model not in self.report.exhausted:
            self.report.exhausted.append(model)
        if outcome.usage.status is LlmCallStatus.TIMEOUT and model not in self.report.timed_out:
            self.report.timed_out.append(model)
        return outcome

    def unavailable(self, model: str) -> bool:
        return model in self.report.exhausted or model in self.report.timed_out


def _screen(chain: Chain, job: Job, item: PlannedJob, roster: Roster, folder: Path) -> (
    ScreeningRecord | str | None
):  # fmt: skip
    """The screening answer (cached or new); a stop reason (str); or None (failed)."""
    if cached := _load_screening(folder, item.key):
        return cached
    settings = chain.settings
    if settings.model in chain.report.exhausted:
        return "the screening model reached its daily limit; run again after midnight Pacific"
    outcome = chain.call(settings.model, item.call, "screen")
    if outcome.usage.status is LlmCallStatus.DAILY_QUOTA:
        return "the screening model reached its daily limit; run again after midnight Pacific"
    if outcome.response is None:
        chain.echo(f"  {job.job_id}: no usable screening answer: {outcome.usage.note}")
        return None
    screened = parse_screen(outcome.response.text, roster, settings.top_k)
    if screened.output is None:
        chain.echo(f"  {job.job_id}: no usable screening answer: {screened.error}")
        return None
    record = ScreeningRecord(
        job_id=job.job_id, model=outcome.response.model, judge_key=item.key,
        usage=outcome.usage, screened=screened, raw_text=outcome.response.text,
    )  # fmt: skip
    _save_screening(folder, record)
    return record


def _review(
    chain: Chain, job: Job, shortlisted: Roster
) -> tuple[ReviewOutput, CallOutcome] | str | None:
    """The deep review by the first model with quota left that answers in time; a stop reason;
    or None (failed). A model that reached its daily limit or timed out is skipped for the
    rest of the run (user decisions 2026-11-08)."""
    call = prepare_review(job_card(job, chain.settings.payload), shortlisted, chain.settings)
    for model in chain.settings.review_chain:
        if chain.unavailable(model):
            continue
        outcome = chain.call(model, call, "review")
        if outcome.usage.status is LlmCallStatus.DAILY_QUOTA:
            chain.echo(f"  {model} reached its daily limit: trying the next review model")
            continue
        if outcome.usage.status is LlmCallStatus.TIMEOUT:
            chain.echo(f"  {model} did not answer in time: trying the next review model")
            continue
        if outcome.response is None:
            chain.echo(f"  {job.job_id}: no usable review answer: {outcome.usage.note}")
            return None
        review, error = parse_review(outcome.response.text)
        if review is None:
            chain.echo(f"  {job.job_id}: no usable review answer: {error}")
            return None
        return review, outcome
    if chain.report.timed_out:
        return "no review model is available (daily limit reached or not answering in time)"
    return "every review model reached its daily limit; run again after midnight Pacific"


def judge_one(
    chain: Chain, job: Job, item: PlannedJob, roster: Roster, folder: Path
) -> TrialResult | str | None:
    """Screen one job, review its shortlist, save the joined answer. Returns the result; a
    stop reason (str: a daily limit, the run should stop); or None (this job failed)."""
    screening = _screen(chain, job, item, roster, folder)
    if not isinstance(screening, ScreeningRecord):
        return screening
    settings = chain.settings
    screened = screening.screened
    assert screened.output is not None
    job_text = render_job(job_card(job, settings.payload))
    review_model = review_usage = review_text = None
    review: ReviewOutput | None = None
    if screened.output.shortlist:
        reviewed = _review(chain, job, roster.subset(screened.output.shortlist))
        if not isinstance(reviewed, tuple):
            return reviewed
        review, outcome = reviewed
        assert outcome.response is not None
        review_model = outcome.response.model
        review_usage, review_text = outcome.usage, outcome.response.text
        chain.report.reviews += 1
    judged = join_answers(screened, review, job_text, roster, settings.top_k)
    result = TrialResult(
        job_id=job.job_id, model=review_model or screening.model, judge_key=item.key,
        usage=screening.usage, aliases=roster.aliases, judged=judged,
        raw_text=screening.raw_text, review_model=review_model, review_usage=review_usage,
        review_raw_text=review_text,
    )  # fmt: skip
    save_judgment(folder, result)
    return result


def judge_jobs(
    jobs: Sequence[Job],
    roster: Roster,
    settings: LlmSettings,
    provider: LLMProvider,
    ledger: UsageLedger,
    folder: Path,
    *,
    max_calls: int | None = None,
    echo: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> JudgeRunReport:
    """`max_calls` caps the number of jobs started (each is one screening call, plus one
    review call when someone is shortlisted)."""
    by_id = {j.job_id: j for j in jobs}
    planned = plan_jobs(jobs, roster, settings, folder)
    report = JudgeRunReport(
        planned=len(planned), cached=sum(p.cached for p in planned), sent=0, ok=0, failed=0
    )
    chain = Chain(settings, provider, ledger, report, echo=echo, sleep=sleep, clock=clock)
    started = 0
    for item in planned:
        if item.cached:
            continue
        if max_calls is not None and started >= max_calls:
            report.stopped = f"--max-calls {max_calls} reached"
            break
        started += 1
        outcome = judge_one(chain, by_id[item.job_id], item, roster, folder)
        if isinstance(outcome, str):
            report.failed += 1
            report.stopped = outcome
            break
        if outcome is None or outcome.judged is None or outcome.judged.output is None:
            report.failed += 1
            continue
        report.ok += 1
        for problem in outcome.judged.problems:
            echo(f"  {item.job_id}: problem: {problem}")
    return report
