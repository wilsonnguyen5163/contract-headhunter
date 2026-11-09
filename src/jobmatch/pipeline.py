"""One full v1 run (phase 6): contractors -> jobs -> Gemini -> shortlists. Used by the
run-once command and, later, by the desktop app's "Find matches" button.

    1. contractors: upsert the contractor file a person picked, if any (no network)
    2. jobs: ingest posting links and pasted postings (fetches only new links)
    3. budget: estimate the calls still needed and compare with today's quota
    4. Gemini: judge each active job not judged yet (cached answers cost nothing)
    5. store: shortlists to data/llm_matches.csv, job status matched / no_match
    6. log: one summary row in data/runs.csv

v1 never uses the v0 matcher: nothing here imports embed/ or match/.

The run holds data/.pipeline.lock, so two runs never overlap (ingest takes its own lock
inside). It is safe to re-run: ingest skips known jobs, judged jobs are read from the cache,
match rows are keyed on judge_key + talent ID, and jobs.csv is rewritten only when a status
really changes.

Callers decide on the budget through `confirm`: it is asked when the run would use most of
today's quota ("warn") or cannot finish today ("exceeds"; confirming then runs only the
calls that fit). Without a yes, no call is sent and the shortlists come from the cache.
"""

import statistics
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from filelock import FileLock, Timeout
from pydantic import BaseModel

from jobmatch.config import LlmSettings, Settings
from jobmatch.ingest import IngestReport, ingest_links, ingest_paste_files
from jobmatch.llm.anonymize import build_roster
from jobmatch.llm.judge import JudgeRunReport, judge_jobs, judgments_dir, plan_jobs
from jobmatch.llm.payload import is_active
from jobmatch.llm.prompt import PROMPT_VERSION
from jobmatch.llm.provider import LLMProvider
from jobmatch.llm.usage import RunCheck, UsageLedger, check_run
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.schemas import JobStatus, Run, RunStatus
from jobmatch.shortlists import absorb_cached, contractors_to_scan, pair_states, sync_rounds
from jobmatch.sources.contractor_upsert import (
    UpsertReport,
    load_contractor_file,
    upsert_contractors,
)
from jobmatch.sources.web import PageFetcher
from jobmatch.storage.loader import create_loader
from jobmatch.storage.repositories import DataLoader

PIPELINE_LOCK_FILE = ".pipeline.lock"

# Progress shares of a run, for a progress bar (0 to 1). The job import counts as one step;
# the Gemini calls share the middle, one step per call.
PROGRESS_CONTRACTORS = 0.05
PROGRESS_IMPORTED = 0.20
PROGRESS_JUDGED = 0.95

Progress = Callable[[float, str], None]


class PipelineError(Exception):
    """The run cannot start or continue (busy, no settings, no contractors...)."""


class RunOnceReport(BaseModel):
    run_id: str
    contractors: UpsertReport | None = None
    ingest: list[IngestReport] = []
    budget: RunCheck | None = None
    confirmed: bool | None = None  # None: no question was needed
    judge: JudgeRunReport | None = None
    active_jobs: int = 0
    jobs_matched: int = 0
    jobs_no_match: int = 0
    jobs_not_judged: int = 0  # no usable answer yet (not sent, failed, or quota)
    matches_added: int = 0
    stopped: str | None = None
    seconds: float = 0.0


def inbox_folders(data_dir: Path) -> tuple[list[Path], list[Path]]:
    """(link folders, paste folders) under data/inbox/ that exist."""
    links, manual = data_dir / "inbox" / "links", data_dir / "inbox" / "manual"
    return ([links] if links.is_dir() else []), ([manual] if manual.is_dir() else [])


def run_once(
    settings: Settings,
    *,
    provider_factory: Callable[[], LLMProvider],
    contractor_file: Path | None = None,
    link_paths: Sequence[Path] | None = None,
    paste_paths: Sequence[Path] | None = None,
    confirm: Callable[[RunCheck], bool] = lambda check: False,
    max_calls: int | None = None,
    echo: Callable[[str], None] = print,
    fetcher_factory: Callable[[], PageFetcher] | None = None,
    progress: Progress | None = None,
) -> RunOnceReport:
    """link_paths / paste_paths default to data/inbox/links and data/inbox/manual.
    provider_factory is only called when a call is about to be sent, so a run that needs
    no call works without an API key. fetcher_factory is for tests (default: WebFetcher).
    progress(share, stage) reports how far the run is (0 to 1), for a progress bar."""
    llm = settings.llm
    if llm is None:
        raise PipelineError("settings.yaml has no llm section")
    data_dir = settings.paths.data_dir
    timeout = settings.storage.lock_timeout_seconds
    now = datetime.now(UTC)
    started = perf_counter()
    report = RunOnceReport(run_id=f"run-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}")
    data_dir.mkdir(parents=True, exist_ok=True)
    loader = create_loader(settings)
    try:
        with FileLock(str(data_dir / PIPELINE_LOCK_FILE), timeout=timeout):
            on_progress = progress or (lambda share, stage: None)
            _run(settings, loader, report, provider_factory, contractor_file, link_paths,
                 paste_paths, confirm, max_calls, echo, fetcher_factory, on_progress)  # fmt: skip
    except Timeout as exc:
        raise PipelineError("another run is in progress; try again when it ends") from exc
    report.seconds = round(perf_counter() - started, 3)
    loader.runs.append(_run_row(report, now, llm.model))
    return report


def _run(
    settings: Settings,
    loader: DataLoader,
    report: RunOnceReport,
    provider_factory: Callable[[], LLMProvider],
    contractor_file: Path | None,
    link_paths: Sequence[Path] | None,
    paste_paths: Sequence[Path] | None,
    confirm: Callable[[RunCheck], bool],
    max_calls: int | None,
    echo: Callable[[str], None],
    fetcher_factory: Callable[[], PageFetcher] | None,
    progress: Progress,
) -> None:
    llm = settings.llm
    assert llm is not None
    data_dir = settings.paths.data_dir
    timeout = settings.storage.lock_timeout_seconds

    # 1. contractors
    progress(0.0, "Loading contractors")
    if contractor_file is not None:
        echo(f"Loading contractors from {contractor_file.name}")
        stored = loader.contractors.list_all() if loader.contractors.exists() else []
        loaded = load_contractor_file(
            contractor_file,
            config_dir=settings.config_dir,
            lock_timeout=timeout,
            existing=stored,
        )
        if loaded.given_ids:  # IDs only, never names
            echo(
                f"  {len(loaded.given_ids)} row(s) had no talent ID and got: "
                f"{', '.join(loaded.given_ids)} (add them to your file to keep them stable)"
            )
        report.contractors = upsert_contractors(loaded.rows, loader.contractors)
        c = report.contractors
        echo(
            f"  {len(c.added)} added · {len(c.updated)} updated · {c.unchanged} unchanged · "
            f"{c.kept_not_in_file} kept (not in the file)"
        )
    if not loader.contractors.exists():
        raise PipelineError("no contractor list yet: load a contractor file first")

    # 2. jobs
    progress(PROGRESS_CONTRACTORS, "Importing jobs")
    default_links, default_pastes = inbox_folders(data_dir)
    links = list(link_paths) if link_paths is not None else default_links
    pastes = list(paste_paths) if paste_paths is not None else default_pastes
    normalizer = Normalizer.from_settings(settings) if links or pastes else None
    if links and normalizer:
        echo("Importing posting links")
        fetcher = fetcher_factory() if fetcher_factory else _web_fetcher(settings)
        try:
            ingest = ingest_links(
                links, data_dir=data_dir, lock_timeout=timeout, normalizer=normalizer,
                fetcher=fetcher, loader=loader,
            )  # fmt: skip
        finally:
            close = getattr(fetcher, "close", None)
            if close:
                close()
        report.ingest.append(ingest)
        echo(f"  {_ingest_line(ingest)}")
    if pastes and normalizer:
        echo("Importing pasted postings")
        ingest = ingest_paste_files(
            pastes, data_dir=data_dir, lock_timeout=timeout, normalizer=normalizer, loader=loader
        )
        report.ingest.append(ingest)
        echo(f"  {_ingest_line(ingest)}")

    # 3. budget
    progress(PROGRESS_IMPORTED, "Checking today's Gemini quota")
    jobs_repo = loader.jobs
    jobs = [j for j in jobs_repo.list_all() if is_active(j)]
    profiles = loader.contractors.list_all()
    roster = build_roster(profiles, llm.payload)
    for ex in roster.excluded:
        echo(f"  contractor left out of the call: {ex.talent_id} ({ex.reason})")
    if not roster.cards:
        raise PipelineError("no contractor can be sent (all left out); check the contractor list")
    report.active_jobs = len(jobs)
    folder = judgments_dir(data_dir)
    # 0.4: each job is sent only the contractors not judged for it yet (shortlists.py).
    states = pair_states(loader)
    rosters = contractors_to_scan(jobs, roster, llm, loader.job_scans.list_all(), states)
    plan = plan_jobs(jobs, roster, llm, folder, rosters)
    todo = [p for p in plan if not p.cached]
    ledger = UsageLedger(loader.llm_usage)
    calls = min(len(todo), max_calls) if max_calls else len(todo)
    if calls:
        per_call = int(statistics.median(p.call.estimated_total_tokens for p in todo))
        # The screening model makes one call per job; reviews use their own models' quotas
        # and fall back down the chain on Google's daily-limit answer.
        check = check_run(llm.limits, llm.budget, ledger.today(model=llm.model), calls, per_call)
        report.budget = check
        cached = len(plan) - len(todo)
        echo(f"{len(jobs)} active job(s) · {calls} to send to Gemini · {cached} cached")
        echo(f"Screening: {llm.model} · deep review: {' → '.join(llm.review_chain)}")
        echo(f"Budget: {check.verdict.upper()} · {check.message}")
        go = True
        if check.verdict != "ok":
            go = report.confirmed = confirm(check)
            if go and check.verdict == "exceeds":
                calls = check.calls_that_fit
                go = calls > 0
        if not go:
            report.stopped = "not sent: the run needs a yes to use that much of today's quota"
            echo(report.stopped)
        else:
            # 4. Gemini: every job is screened first (one "[llm] screen" line each), then the
            # shortlists are reviewed in batches ("[llm] review" lines; at most one per batch,
            # fewer when some jobs have nobody shortlisted). One progress step per line.
            screened = reviewed = 0
            batches = -(-calls // llm.review_batch_size)  # at most this many review calls
            steps = calls + batches
            progress(PROGRESS_IMPORTED, f"Screening jobs: 0 of {calls}")

            def echo_call(line: str) -> None:
                nonlocal screened, reviewed
                echo(line)
                if line.startswith("[llm] screen"):
                    screened += 1
                    label = f"Screening jobs: {screened} of {calls}"
                elif line.startswith("[llm] review"):
                    reviewed += 1
                    label = f"Deep reviews: {reviewed} of up to {batches}"
                else:
                    return
                done = screened + reviewed
                share = PROGRESS_IMPORTED + (PROGRESS_JUDGED - PROGRESS_IMPORTED) * done / steps
                progress(min(share, PROGRESS_JUDGED), label)

            report.judge = judge_jobs(
                jobs, roster, llm, provider_factory(), ledger, folder, max_calls=calls,
                echo=echo_call, rosters=rosters,
            )  # fmt: skip
            j = report.judge
            echo(
                f"{j.sent} call(s) sent · {j.ok} usable · {j.failed} failed · tokens in "
                f"{j.input_tokens:,} · out {j.output_tokens:,} · total {j.total_tokens:,}"
            )
            echo_today(llm, ledger, echo)
            if j.exhausted:
                echo(f"Reached the daily limit today: {', '.join(j.exhausted)}")
            if j.timed_out:
                echo(f"Did not answer in time (skipped for this run): {', '.join(j.timed_out)}")
            if j.stopped:
                report.stopped = j.stopped
    else:
        echo(f"{len(jobs)} active job(s) · nobody new to scan (no API call)")

    # 5. store every candidate (the pool) and who was scanned, then the rounds and statuses
    progress(PROGRESS_JUDGED, "Saving shortlists")
    now = datetime.now(UTC)
    report.matches_added = absorb_cached(settings, loader, now)
    pictures = sync_rounds(settings, loader, now)
    statuses: dict[str, tuple[JobStatus, str | None]] = {}
    for job in jobs:
        picture = pictures[job.job_id]
        if picture.to_scan:
            report.jobs_not_judged += 1
            continue
        status = JobStatus.MATCHED if picture.shortlist.shown else JobStatus.NO_MATCH
        if status is JobStatus.MATCHED:
            report.jobs_matched += 1
        else:
            report.jobs_no_match += 1
        if job.status is not status:
            statuses[job.job_id] = (status, None)
    jobs_repo.update_statuses(statuses)
    echo(
        f"Shortlists: {report.jobs_matched} job(s) with matches · {report.jobs_no_match} with "
        f"none · {report.jobs_not_judged} not judged yet · {report.matches_added} new row(s)"
    )
    progress(1.0, "Done")


def echo_today(llm: LlmSettings, ledger: UsageLedger, echo: Callable[[str], None]) -> None:
    """Today's usage, total and per model used (each model has its own daily limits)."""
    total = ledger.today()
    echo(f"Today: {total.requests} requests · {total.tokens:,} tokens")
    for model in dict.fromkeys([llm.model, *llm.review_chain]):
        day = ledger.today(model=model)
        if day.requests:
            limits = llm.for_model(model).limits
            echo(f"  {model}: {day.requests}/{limits.rpd} requests · {day.tokens:,} tokens")


def _web_fetcher(settings: Settings) -> PageFetcher:
    from jobmatch.config import load_sources
    from jobmatch.sources.web import WebFetcher

    return WebFetcher(load_sources(settings.config_dir).web)


def _ingest_line(report: IngestReport) -> str:
    parts = [
        f"{report.count(o)} {label}"
        for o, label in (
            ("new", "new"),
            ("needs_details", "need details"),
            ("filtered", "filtered out"),
            ("duplicate", "duplicate"),
            ("already_stored", "already stored"),
            ("skipped", "skipped"),
            ("error", "error(s)"),
        )  # fmt: skip
        if report.count(o)  # type: ignore[arg-type]
    ]
    return ", ".join(parts) or "nothing new"


def _run_row(report: RunOnceReport, started_at: datetime, model: str) -> Run:
    failed = report.judge.failed if report.judge else 0
    ingest_errors = sum(r.count("error") for r in report.ingest)
    partial = bool(failed or ingest_errors or report.stopped or report.jobs_not_judged)
    return Run(
        run_id=report.run_id,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        status=RunStatus.PARTIAL if partial else RunStatus.OK,
        source_counts=[f"llm_calls={report.judge.sent if report.judge else 0}"],
        fetched=sum(r.fetched for r in report.ingest),
        new_jobs=sum(r.count("new") + r.count("needs_details") for r in report.ingest),
        filtered_out=sum(r.count("filtered") + r.count("duplicate") for r in report.ingest),
        matched=report.jobs_matched,
        errors=failed + ingest_errors,
        duration_seconds=report.seconds,
        model_name=model,
        model_version=PROMPT_VERSION,
    )
