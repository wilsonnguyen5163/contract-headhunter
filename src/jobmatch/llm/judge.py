"""Judge many jobs, one call each, with every guard (phase 5 pilot; phase 6 builds on it).

- Each answer is cached under data/llm_results/judgments/<judge_key>.json. The key covers
  the model, prompt version, job text and roster, so a re-run only pays for what changed.
- Calls are paced to stay under RPM and TPM (usage.Pacer).
- A daily-limit 429 stops the run; the next run picks up where it stopped.
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
from jobmatch.llm.prompt import PreparedCall, prepare_call
from jobmatch.llm.provider import LLMProvider
from jobmatch.llm.runner import send
from jobmatch.llm.trial import TrialResult, judge_answer
from jobmatch.llm.usage import Pacer, UsageLedger
from jobmatch.schemas import Job, LlmCallStatus
from jobmatch.storage.files import atomic_write_text

JUDGMENTS_DIR = Path("llm_results") / "judgments"  # under data_dir


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


class PlannedJob(BaseModel):
    job_id: str
    call: PreparedCall
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
    sent: int
    ok: int  # answers that parsed
    failed: int  # calls or answers that did not
    stopped: str | None = None  # why the run stopped early
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


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
    by_id = {j.job_id: j for j in jobs}
    planned = plan_jobs(jobs, roster, settings, folder)
    report = JudgeRunReport(
        planned=len(planned), cached=sum(p.cached for p in planned), sent=0, ok=0, failed=0
    )
    pacer = Pacer(settings.limits)
    for item in planned:
        if item.cached:
            continue
        if max_calls is not None and report.sent >= max_calls:
            report.stopped = f"--max-calls {max_calls} reached"
            break
        wait = pacer.wait_seconds(clock(), item.call.estimated_total_tokens)
        if wait > 0:
            sleep(wait)
        outcome = send(
            provider, ledger, settings, item.call.request,
            purpose="judge", job_ids=item.call.job_ids,
            estimated_tokens=item.call.estimated_total_tokens, clock=clock,
        )  # fmt: skip
        echo(outcome.line)
        if outcome.usage.attempts:
            report.sent += 1
            pacer.record(clock(), outcome.usage.total_tokens or item.call.estimated_total_tokens)
        report.input_tokens += outcome.usage.input_tokens
        report.output_tokens += outcome.usage.output_tokens
        report.total_tokens += outcome.usage.total_tokens
        if outcome.usage.status is LlmCallStatus.DAILY_QUOTA:
            report.failed += 1
            report.stopped = "daily limit reached; run again after midnight Pacific time"
            break
        judged = None
        if outcome.response is not None:
            card = job_card(by_id[item.job_id], settings.payload)
            judged = judge_answer(outcome.response.text, render_job(card), roster, settings.top_k)
        result = TrialResult(
            job_id=item.job_id,
            model=outcome.response.model if outcome.response else settings.model,
            judge_key=item.key,
            usage=outcome.usage,
            aliases=roster.aliases,
            judged=judged,
            raw_text=outcome.response.text if outcome.response else None,
        )
        save_judgment(folder, result)
        if judged is not None and judged.output is not None:
            report.ok += 1
            for problem in judged.problems:
                echo(f"  {item.job_id}: problem: {problem}")
        else:
            report.failed += 1
            reason = judged.error if judged else outcome.usage.note
            echo(f"  {item.job_id}: no usable answer: {reason}")
    return report
