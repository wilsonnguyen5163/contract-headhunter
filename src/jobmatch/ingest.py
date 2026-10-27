"""Ingest postings from posting links or paste files, through one shared path:

    links (data/inbox/links/*.txt) --fetch or cache--> RawJob ┐
    paste files and saved pages (data/inbox/manual/*.txt, *.html, *.pdf) -> RawJob ┴-> raw store
                                                     -> normalize
                                                     -> dedupe -> jobs.csv -> runs.csv

One run holds data/.run.lock so two runs never overlap. Re-running is safe: identical raw
records are not stored twice, known jobs are skipped, and a link fetched before is read
from the raw store instead of the network (unless refresh=True). A bad file or link is
reported and skipped; the others are still ingested.
"""

import hashlib
import json
import logging
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, get_args

from filelock import FileLock
from pydantic import BaseModel

from jobmatch.normalize.dedupe import DedupeDecision, DedupeOutcome, dedupe
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.schemas import JobStatus, RawJob, RawRecord, Run, RunStatus
from jobmatch.sources.links import collect_link_files, read_links
from jobmatch.sources.paste import PasteFormatError, canonical_url, read_paste_file
from jobmatch.sources.saved_files import SAVED_SUFFIXES, read_saved_file
from jobmatch.sources.web import FetchError, PageFetcher
from jobmatch.sources.web_page import PageData, PageError, extract_page, raw_job_from_page
from jobmatch.storage.csv_store import CsvJobRepository, CsvRunRepository, CsvStore
from jobmatch.storage.raw_store import RawStore

log = logging.getLogger("jobmatch.ingest")

RUN_LOCK_FILE = ".run.lock"
PASTE_SUFFIX = ".txt"
LINK_KIND = "link"
GONE_KIND = "gone"  # a link that answered 404/410: dropped, not fetched again
GONE_STATUSES = (404, 410)

Outcome = Literal[
    "new", "needs_details", "filtered", "duplicate", "filled_stub", "already_stored", "error",
    "skipped",
]  # fmt: skip


class FileOutcome(BaseModel):
    file: str  # a paste file, or "links-file.txt:<line>" for a link
    outcome: Outcome
    job_id: str | None = None
    detail: str | None = None


class IngestReport(BaseModel):
    run_id: str
    dry_run: bool
    outcomes: list[FileOutcome]
    fetched: int = 0  # pages requested from the network in this run

    def count(self, outcome: Outcome) -> int:
        return sum(o.outcome == outcome for o in self.outcomes)


class _Item(BaseModel):
    label: str
    raw_job: RawJob
    record: RawRecord | None  # None when re-using a stored record (nothing new to keep)


# ---- paste files ----


def collect_paste_files(paths: Iterable[Path]) -> tuple[list[Path], list[FileOutcome]]:
    """Expand folders to their paste files (*.txt) and saved postings (*.html, *.htm, *.pdf);
    skip names starting with "_" (templates)."""
    suffixes = (PASTE_SUFFIX, *SAVED_SUFFIXES)
    files: list[Path] = []
    skipped: list[FileOutcome] = []
    for path in paths:
        candidates = (
            sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in suffixes)
            if path.is_dir()
            else [path]
        )
        for candidate in candidates:
            if candidate.name.startswith("_"):
                skipped.append(
                    FileOutcome(file=str(candidate), outcome="skipped", detail="template")
                )
            elif not candidate.is_file():
                skipped.append(
                    FileOutcome(file=str(candidate), outcome="error", detail="not found")
                )
            else:
                files.append(candidate)
    return files, skipped


def ingest_paste_files(
    paths: Sequence[Path],
    *,
    data_dir: Path,
    lock_timeout: float,
    normalizer: Normalizer,
    now: datetime | None = None,
    dry_run: bool = False,
) -> IngestReport:
    now = now or datetime.now(UTC)
    started = perf_counter()
    files, outcomes = collect_paste_files(paths)
    items: list[_Item] = []
    for path in files:
        if _is_empty(path):
            outcomes.append(FileOutcome(file=str(path), outcome="skipped", detail="empty file"))
            continue
        try:
            if path.suffix.lower() in SAVED_SUFFIXES:
                raw_job, record = read_saved_file(path, fetched_at=now)
            else:
                raw_job, record = read_paste_file(path, fetched_at=now)
        except (PasteFormatError, OSError) as exc:
            outcomes.append(FileOutcome(file=str(path), outcome="error", detail=str(exc)))
            continue
        items.append(_Item(label=str(path), raw_job=raw_job, record=record))
    return _store(
        items, outcomes, data_dir=data_dir, lock_timeout=lock_timeout, normalizer=normalizer,
        now=now, dry_run=dry_run, started=started, source_name="paste", fetched=0,
    )  # fmt: skip


# ---- posting links ----


def ingest_links(
    paths: Sequence[Path],
    *,
    data_dir: Path,
    lock_timeout: float,
    normalizer: Normalizer,
    fetcher: PageFetcher,
    now: datetime | None = None,
    dry_run: bool = False,
    refresh: bool = False,
) -> IngestReport:
    now = now or datetime.now(UTC)
    started = perf_counter()
    files, missing = collect_link_files(list(paths))
    outcomes = [FileOutcome(file=str(p), outcome="error", detail="not found") for p in missing]
    raw_store = RawStore(data_dir / "raw", lock_timeout=lock_timeout)
    cache: dict[str, dict[str, RawRecord]] = {}
    gone: dict[str, dict[str, RawRecord]] = {}
    gone_records: list[RawRecord] = []
    seen: set[tuple[str, str]] = set()
    items: list[_Item] = []
    fetched = 0

    for path in files:
        for entry in read_links(path):
            try:
                source, source_id, url = canonical_url(entry.url)
            except PasteFormatError as exc:
                outcomes.append(FileOutcome(file=entry.label, outcome="error", detail=str(exc)))
                continue
            if (source, source_id) in seen:
                outcomes.append(
                    FileOutcome(file=entry.label, outcome="skipped", detail="listed twice")
                )
                continue
            seen.add((source, source_id))
            reason = fetcher.blocked_reason(url)
            if reason:
                outcomes.append(FileOutcome(file=entry.label, outcome="skipped", detail=reason))
                continue

            if source not in cache:
                cache[source] = raw_store.latest(source, kind=LINK_KIND)
                gone[source] = raw_store.latest(source, kind=GONE_KIND)
            stored = None if refresh else cache[source].get(source_id)
            dead = None if refresh else gone[source].get(source_id)
            if dead is not None and (stored is None or dead.fetched_at >= stored.fetched_at):
                status = dead.payload.get("http_status")
                outcomes.append(
                    FileOutcome(
                        file=entry.label,
                        outcome="skipped",
                        detail=f"page gone (HTTP {status}) since {dead.fetched_at:%Y-%m-%d}; "
                        "not fetched again (--refresh retries)",
                    )
                )
                continue
            if stored is not None:
                payload, fetched_at, record = stored.payload, stored.fetched_at, None
            else:
                try:
                    page = fetcher.fetch(url)
                except FetchError as exc:
                    fetched += 1
                    if exc.status in GONE_STATUSES:
                        gone_records.append(
                            _record(
                                source,
                                source_id,
                                now,
                                {
                                    "kind": GONE_KIND,
                                    "requested_url": entry.url,
                                    "http_status": exc.status,
                                },  # fmt: skip
                            )
                        )
                        outcomes.append(
                            FileOutcome(
                                file=entry.label,
                                outcome="skipped",
                                detail=f"page gone (HTTP {exc.status}): dropped, not fetched again",
                            )
                        )
                    else:
                        outcomes.append(
                            FileOutcome(file=entry.label, outcome="error", detail=f"fetch: {exc}")
                        )
                    continue
                fetched += 1
                data = extract_page(page.html)
                payload = _link_payload(entry.url, page.final_url, page.status, data)
                fetched_at = now
                record = _record(source, source_id, now, payload)
            try:
                raw_job = raw_job_from_page(
                    PageData.model_validate(payload["page"]),
                    source=source,
                    source_id=source_id,
                    url=url,
                    fetched_at=fetched_at,
                )
            except (PageError, ValueError) as exc:
                outcomes.append(FileOutcome(file=entry.label, outcome="error", detail=str(exc)))
                continue
            items.append(_Item(label=entry.label, raw_job=raw_job, record=record))

    return _store(
        items, outcomes, data_dir=data_dir, lock_timeout=lock_timeout, normalizer=normalizer,
        now=now, dry_run=dry_run, started=started, source_name="links", fetched=fetched,
        extra_records=gone_records,
    )  # fmt: skip


def _link_payload(requested: str, final: str, status: int, page: PageData) -> dict[str, Any]:
    return {
        "kind": LINK_KIND,
        "requested_url": requested,
        "final_url": final,
        "http_status": status,
        "page": page.model_dump(),
    }


def _record(source: str, source_id: str, now: datetime, payload: dict[str, Any]) -> RawRecord:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return RawRecord(
        source=source, source_id=source_id, fetched_at=now, content_hash=digest, payload=payload
    )


def _is_empty(path: Path) -> bool:
    try:
        if path.suffix.lower() in SAVED_SUFFIXES:
            return path.stat().st_size == 0
        return not path.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return False  # reported as an error by the reader


# ---- shared: normalize, dedupe, store ----


def _store(
    items: list[_Item],
    outcomes: list[FileOutcome],
    *,
    data_dir: Path,
    lock_timeout: float,
    normalizer: Normalizer,
    now: datetime,
    dry_run: bool,
    started: float,
    source_name: str,
    fetched: int,
    extra_records: list[RawRecord] | None = None,
) -> IngestReport:
    run_id = f"ingest-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    data_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(data_dir / RUN_LOCK_FILE), timeout=lock_timeout):
        store = CsvStore(data_dir, lock_timeout=lock_timeout)
        jobs_repo = CsvJobRepository(store)
        jobs = [normalizer.normalize(item.raw_job, now=now) for item in items]
        result = dedupe(jobs_repo.list_all(), jobs, similarity=normalizer.title_similarity)

        for item, job, decision in zip(items, jobs, result.outcomes, strict=True):
            outcomes.append(_file_outcome(item.label, job.status, job.filter_reason, decision))
        report = IngestReport(run_id=run_id, dry_run=dry_run, outcomes=outcomes, fetched=fetched)

        if not dry_run:
            raw_store = RawStore(data_dir / "raw", lock_timeout=lock_timeout)
            raw_store.append(
                [item.record for item in items if item.record is not None]
                + list(extra_records or [])
            )
            jobs_repo.add_new(result.to_add)
            jobs_repo.replace(result.to_replace)
            CsvRunRepository(store).append(
                _run_row(report, now, perf_counter() - started, source_name)
            )

    counts = {outcome: report.count(outcome) for outcome in get_args(Outcome)}
    log.info(
        "ingest_done",
        extra={"fields": {"run_id": run_id, "dry_run": dry_run, "fetched": fetched, **counts}},
    )
    return report


def _file_outcome(
    label: str, status: JobStatus, filter_reason: str | None, decision: DedupeOutcome
) -> FileOutcome:
    match decision.decision:
        case DedupeDecision.ALREADY_STORED:
            return FileOutcome(file=label, outcome="already_stored", job_id=decision.job_id)
        case DedupeDecision.FILLS_STUB:
            return FileOutcome(file=label, outcome="filled_stub", job_id=decision.job_id)
        case DedupeDecision.DUPLICATE:
            return FileOutcome(
                file=label,
                outcome="duplicate",
                job_id=decision.job_id,
                detail=f"same job as {decision.other_job_id}"
                + (" (similar title)" if decision.similar_title else ""),
            )
        case DedupeDecision.SUPERSEDES_STUB:
            return FileOutcome(
                file=label,
                outcome="new",
                job_id=decision.job_id,
                detail=f"completes stub {decision.other_job_id}",
            )
    if status is JobStatus.FILTERED_OUT:
        return FileOutcome(
            file=label, outcome="filtered", job_id=decision.job_id, detail=filter_reason
        )
    if status is JobStatus.NEEDS_DETAILS:
        return FileOutcome(file=label, outcome="needs_details", job_id=decision.job_id)
    return FileOutcome(file=label, outcome="new", job_id=decision.job_id)


def _run_row(report: IngestReport, now: datetime, seconds: float, source_name: str) -> Run:
    errors = report.count("error")
    stored = [o for o in report.outcomes if o.outcome not in ("error", "skipped")]
    return Run(
        run_id=report.run_id,
        started_at=now,
        finished_at=datetime.now(UTC),
        status=RunStatus.PARTIAL if errors else RunStatus.OK,
        source_counts=[f"{source_name}={len(stored)}"],
        fetched=report.fetched if source_name == "links" else len(stored),
        new_jobs=report.count("new") + report.count("needs_details"),
        filtered_out=report.count("filtered") + report.count("duplicate"),
        errors=errors,
        duration_seconds=round(seconds, 3),
    )
