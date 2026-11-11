"""Judge many jobs with every guard (v12 two-stage design, user 2026-11-08; v15 batches).

1. Screening (llm.model), one call per job: every contractor, one-line verdicts, a shortlist
   of those who fit or partly fit (up to top_k; empty when nobody fits: no review needed).
2. Deep review of the shortlists, llm.review_batch_size jobs per call (each job judged only
   against its own shortlist and text), by the first model in llm.review_models that still
   has quota today. A batch answer that leaves a job out leaves that job for the next run.
   When Google answers "daily limit reached" for a model, or the model does not answer within
   llm.timeout_seconds, it is skipped for the rest of the run and the next model in the list
   reviews instead (the next run tries it first again).

- Each job's joined answer is cached under data/llm_results/judgments/<judge_key>.json; the
  key covers the screening model, prompt version, thinking setting, job text and roster, so
  a re-run only pays for what changed. The screening answer is also kept on its own
  (judgments/screening/), so a review that failed is retried without screening again.
- Calls are paced per model to stay under its RPM and TPM (usage.Pacer).
- The run stops when the screening model, or every review model, reaches its daily limit;
  the next run picks up where it stopped.
- Nothing here changes jobs.csv or any match file.
"""

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
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


def move_judgments(folder: Path, job_id: str, dest: Path) -> int:
    """Move a job's cached answers (judgments and screenings) out of the cache into `dest`
    (a backup folder), so the job, imported again, is asked afresh (Delete job, user
    decision 2026-11-11). Never deletes. Returns the files moved."""
    moved = 0
    for source in [*folder.glob("*.json"), *(folder / SCREENING_DIR).glob("*.json")]:
        try:
            owner = json.loads(source.read_text(encoding="utf-8")).get("job_id")
        except (OSError, ValueError, AttributeError):
            continue
        if owner != job_id:
            continue
        target = dest / source.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        moved += 1
    return moved


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
    key: str  # the judgment's key (screening + review)
    screen_key: str  # the screening answer's key (changes only with the screening prompt)
    cached: bool
    roster: Roster  # the contractors this job is screened against


def plan_jobs(
    jobs: Sequence[Job],
    roster: Roster,
    settings: LlmSettings,
    folder: Path,
    rosters: Mapping[str, Roster] | None = None,
) -> list[PlannedJob]:
    """One planned screening per job. With `rosters` (job_id -> the contractors not judged
    yet for it, pipeline.contractors_to_scan), each job is screened against its own list, and
    a job missing from it has nobody to send: it is not planned."""
    planned: list[PlannedJob] = []
    for job in jobs:
        if rosters is not None and job.job_id not in rosters:
            continue
        own = rosters[job.job_id] if rosters is not None else roster
        call = prepare_call(job_card(job, settings.payload), own, settings)
        key = call.judge_key(settings.model)
        cached = load_judgment(folder, key) is not None
        planned.append(
            PlannedJob(
                job_id=job.job_id,
                call=call,
                key=key,
                screen_key=call.screen_key(settings.model),
                cached=cached,
                roster=own,
            )  # fmt: skip
        )
    return planned


class JudgeRunReport(BaseModel):
    planned: int
    cached: int
    sent: int  # calls sent (screening and review)
    ok: int  # jobs with a usable answer
    failed: int  # jobs without one
    reviews: int = 0  # jobs reviewed in depth
    review_calls: int = 0  # review calls that answered (each covers up to review_batch_size)
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
        self._providers: dict[tuple[str, str | None], LLMProvider] = {}
        self._pacers: dict[str, Pacer] = {}
        self.ledger = ledger
        self.report = report
        self.echo = echo
        self.sleep = sleep
        self.clock = clock

    def provider(self, model: str, review: bool = False) -> LLMProvider:
        """The run's provider (Gemini) switched to one model of the chain; for a review call
        with llm.review_thinking_level set, with that thinking level."""
        thinking = self.settings.review_thinking_level if review else None
        if (model, thinking) not in self._providers:
            with_model = getattr(self._provider, "with_model", None)
            if with_model is None:
                self._providers[(model, thinking)] = self._provider
            elif thinking is None:
                self._providers[(model, thinking)] = with_model(model)
            else:
                self._providers[(model, thinking)] = with_model(model, thinking)
        return self._providers[(model, thinking)]

    def call(self, model: str, call: PreparedCall, purpose: str) -> CallOutcome:
        settings = self.settings.for_model(model)
        pacer = self._pacers.setdefault(model, Pacer(settings.limits))
        wait = pacer.wait_seconds(self.clock(), call.estimated_total_tokens)
        if wait > 0:
            self.sleep(wait)
        outcome = send(
            self.provider(model, review=purpose == "review"), self.ledger, settings, call.request,
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
    if cached := _load_screening(folder, item.screen_key):
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
    screened = parse_screen(outcome.response.text, item.roster, settings.top_k)
    if screened.output is None:
        chain.echo(f"  {job.job_id}: no usable screening answer: {screened.error}")
        return None
    record = ScreeningRecord(
        job_id=job.job_id, model=outcome.response.model, judge_key=item.screen_key,
        usage=outcome.usage, screened=screened, raw_text=outcome.response.text,
    )  # fmt: skip
    _save_screening(folder, record)
    return record


class _Pending(BaseModel):
    """A screened job waiting for its deep review."""

    job: Job
    item: PlannedJob
    screening: ScreeningRecord


def _save(
    chain: Chain, pending: _Pending, roster: Roster, folder: Path,
    review: ReviewOutput | None, outcome: CallOutcome | None, batch: list[str],
) -> TrialResult:  # fmt: skip
    """Join one job's screening and review (None: nobody shortlisted) and save it. Checked
    against the contractors this job was screened with."""
    settings = chain.settings
    roster = pending.item.roster
    screened = pending.screening.screened
    job_text = render_job(job_card(pending.job, settings.payload))
    judged = join_answers(screened, review, job_text, roster, settings.top_k)
    response = outcome.response if outcome else None
    result = TrialResult(
        job_id=pending.job.job_id,
        model=response.model if response else pending.screening.model,
        judge_key=pending.item.key, usage=pending.screening.usage, aliases=roster.aliases,
        judged=judged, raw_text=pending.screening.raw_text,
        review_model=response.model if response else None,
        review_usage=outcome.usage if outcome else None,
        review_raw_text=response.text if response else None,
        review_batch=batch,
    )  # fmt: skip
    save_judgment(folder, result)
    return result


def _review_batch(
    chain: Chain, batch: list[_Pending], roster: Roster
) -> tuple[dict[str, ReviewOutput], CallOutcome] | str | None:
    """One review call for a batch of screened jobs, by the first model with quota left that
    answers in time: (job_id -> review, outcome); a stop reason; or None (the call failed:
    every job of the batch is retried next run). A model that reached its daily limit or
    timed out is skipped for the rest of the run (user decisions 2026-11-08)."""
    settings = chain.settings
    call = prepare_review(
        [
            (job_card(p.job, settings.payload), p.screening.screened.output.shortlist)  # type: ignore[union-attr]
            for p in batch
        ],
        roster,
        settings,
    )
    names = ", ".join(p.job.job_id for p in batch)
    for model in settings.review_chain:
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
            chain.echo(f"  {names}: no usable review answer: {outcome.usage.note}")
            return None
        reviews, problems, error = parse_review(outcome.response.text, call.labels)
        if error is not None:
            chain.echo(f"  {names}: no usable review answer: {error}")
            return None
        for problem in problems:
            chain.echo(f"  {names}: problem: {problem}")
        chain.report.review_calls += 1
        return reviews, outcome
    if chain.report.timed_out:
        return "no review model is available (daily limit reached or not answering in time)"
    return "every review model reached its daily limit; run again after midnight Pacific"


def _review_all(
    chain: Chain, pending: list[_Pending], roster: Roster, folder: Path
) -> tuple[list[TrialResult], str | None]:
    """Review the screened jobs that have a shortlist, llm.review_batch_size per call. Jobs a
    batch answer leaves out, or whose call fails, are left for the next run."""
    results: list[TrialResult] = []
    size = chain.settings.review_batch_size
    for start in range(0, len(pending), size):
        batch = pending[start : start + size]
        reviewed = _review_batch(chain, batch, roster)
        if isinstance(reviewed, str):
            return results, reviewed
        if reviewed is None:
            continue
        reviews, outcome = reviewed
        ids = [p.job.job_id for p in batch]
        for p in batch:
            if p.job.job_id in reviews:
                results.append(_save(chain, p, roster, folder, reviews[p.job.job_id], outcome, ids))
                chain.report.reviews += 1
    return results, None


def judge_one(
    chain: Chain, job: Job, item: PlannedJob, roster: Roster, folder: Path
) -> TrialResult | str | None:
    """Screen one job and review its shortlist (a batch of one), save the joined answer.
    Returns the result; a stop reason (str: a daily limit, the run should stop); or None
    (this job failed)."""
    screening = _screen(chain, job, item, roster, folder)
    if not isinstance(screening, ScreeningRecord):
        return screening
    pending = _Pending(job=job, item=item, screening=screening)
    assert screening.screened.output is not None
    if not screening.screened.output.shortlist:
        return _save(chain, pending, roster, folder, None, None, [])
    results, stopped = _review_all(chain, [pending], roster, folder)
    if stopped:
        return stopped
    return results[0] if results else None


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
    rosters: Mapping[str, Roster] | None = None,
) -> JudgeRunReport:
    """Screen every job not judged yet, then review the shortlists in batches of
    llm.review_batch_size. `max_calls` caps the number of jobs started. `rosters`: each
    job's own contractors to send (plan_jobs); the review uses `roster`, which holds them
    all under the same aliases."""
    by_id = {j.job_id: j for j in jobs}
    planned = plan_jobs(jobs, roster, settings, folder, rosters)
    report = JudgeRunReport(
        planned=len(planned), cached=sum(p.cached for p in planned), sent=0, ok=0, failed=0
    )
    chain = Chain(settings, provider, ledger, report, echo=echo, sleep=sleep, clock=clock)
    results: list[TrialResult] = []
    pending: list[_Pending] = []
    started = 0
    # 1. screening
    for item in planned:
        if item.cached:
            continue
        if max_calls is not None and started >= max_calls:
            report.stopped = f"--max-calls {max_calls} reached"
            break
        started += 1
        job = by_id[item.job_id]
        screening = _screen(chain, job, item, roster, folder)
        if isinstance(screening, str):
            report.stopped = screening
            break  # the jobs screened so far are still reviewed below
        if screening is None:
            report.failed += 1
            continue
        assert screening.screened.output is not None
        p = _Pending(job=job, item=item, screening=screening)
        if screening.screened.output.shortlist:
            pending.append(p)
        else:
            results.append(_save(chain, p, roster, folder, None, None, []))
    # 2. reviews, in batches
    reviewed, stopped = _review_all(chain, pending, roster, folder)
    results += reviewed
    if stopped and not report.stopped:  # the first reason is the one to act on
        report.stopped = stopped
    report.failed += len(pending) - len(reviewed)
    for result in results:
        if result.judged is None or result.judged.output is None:
            report.failed += 1
            continue
        report.ok += 1
        for problem in result.judged.problems:
            echo(f"  {result.job_id}: problem: {problem}")
    return report
