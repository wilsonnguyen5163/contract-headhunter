"""Everything the desktop app does, without any UI code (so it is tested directly).

The screens (app/ui.py) only call this class. It works on one Settings object whose data
paths point at the app's data root, and on the app's .env file for the Gemini key and the
reviewer name. Nothing here prints or logs contractor names or the key.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from jobmatch import secrets_file
from jobmatch.config import Settings
from jobmatch.llm.export import ExportResult, export_current
from jobmatch.llm.payload import ACTIVE_STATUSES
from jobmatch.llm.provider import LLMProvider
from jobmatch.llm.usage import RunCheck, UsageLedger
from jobmatch.pipeline import RunOnceReport, current_judge_keys, run_once
from jobmatch.schemas import (
    ContractorProfile,
    Decision,
    Job,
    LlmMatch,
    LlmVerdict,
    ReasonCode,
    Review,
)
from jobmatch.sources.contractor_upsert import read_contractor_file
from jobmatch.sources.saved_files import SAVED_SUFFIXES
from jobmatch.storage.csv_store import to_cell
from jobmatch.storage.loader import create_loader
from jobmatch.storage.repositories import DataLoader

REVIEWER_ENV = "JOBMATCH_REVIEWER"
DEFAULT_REVIEWER = "reviewer"
JOB_SUFFIXES = (".txt", *SAVED_SUFFIXES)
CONTRACTOR_SUFFIXES = (".xlsx", ".csv")

JobKind = Literal["links", "manual"]
JobOutcome = Literal["fits", "check_partial", "no_match", "not_judged"]


class AppError(Exception):
    """A problem to show to the person, in plain words."""


class AddedJobsFile(BaseModel):
    kind: JobKind  # links: one URL per line; manual: a pasted or saved posting
    dest: Path
    links: int | None = None  # number of URLs, for a links file


class ContractorFileInfo(BaseModel):
    path: Path
    contractors: int


class MatchView(BaseModel):
    match: LlmMatch
    name: str | None
    headline: str | None
    decision: Review | None  # latest decision, if any


class JobResult(BaseModel):
    job: Job
    outcome: JobOutcome
    matches: list[MatchView]


class Quota(BaseModel):
    requests: int
    rpd: int
    tokens: int


def _current_decision(review: Review | None) -> Review | None:
    """The latest decision, or None when there is none or it was reset."""
    return None if review is None or review.decision is Decision.CLEARED else review


def classify_jobs_file(name: str, data: bytes) -> JobKind:
    """links when every non-comment line of a .txt file starts with an http(s) URL;
    manual for anything else (a pasted posting, or a saved .html / .pdf page)."""
    suffix = Path(name).suffix.lower()
    if suffix not in JOB_SUFFIXES:
        raise AppError(f"{name}: choose a .txt, .html or .pdf file")
    if suffix != ".txt":
        return "manual"
    lines = [
        ln.strip()
        for ln in data.decode("utf-8-sig", errors="replace").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if lines and all(ln.split()[0].lower().startswith(("http://", "https://")) for ln in lines):
        return "links"
    return "manual"


def _stamped(folder: Path, name: str, now: datetime) -> Path:
    """A new file name in `folder` that never overwrites one already there."""
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{now:%Y%m%dT%H%M%SZ}-{Path(name).name}"
    dest, n = folder / base, 1
    while dest.exists():
        n += 1
        dest = folder / f"{Path(base).stem}-{n}{Path(base).suffix}"
    return dest


class AppService:
    def __init__(
        self,
        settings: Settings,
        env_path: Path,
        *,
        provider_factory: Callable[[], LLMProvider] | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        if settings.llm is None:
            raise AppError("settings.yaml has no llm section")
        self.settings = settings
        self.llm = settings.llm
        self.env_path = env_path
        self._provider_factory = provider_factory
        self._environ = os.environ if environ is None else environ
        self.contractor_file: Path | None = None  # picked, loaded on the next run

    # ---- paths and stores ----

    @property
    def data_dir(self) -> Path:
        return self.settings.paths.data_dir

    def _loader(self) -> DataLoader:
        return create_loader(self.settings)

    # ---- settings: key and reviewer ----

    def has_key(self) -> bool:
        return bool(self._environ.get(self.llm.api_key_env, "").strip())

    def save_key(self, value: str) -> None:
        value = value.strip()
        if not value:
            raise AppError("The key is empty.")
        secrets_file.set_value(self.env_path, self.llm.api_key_env, value)
        self._environ[self.llm.api_key_env] = value

    def reviewer(self) -> str:
        return self._environ.get(REVIEWER_ENV, "").strip() or DEFAULT_REVIEWER

    def save_reviewer(self, name: str) -> None:
        name = " ".join(name.split())
        if not name:
            raise AppError("The reviewer name is empty.")
        secrets_file.set_value(self.env_path, REVIEWER_ENV, name)
        self._environ[REVIEWER_ENV] = name

    # ---- inputs ----

    def add_jobs_file(self, name: str, data: bytes, now: datetime | None = None) -> AddedJobsFile:
        """Copy a picked jobs file into data/inbox/links or data/inbox/manual (never
        overwriting); the next run imports it."""
        if not data.strip():
            raise AppError(f"{name} is empty.")
        kind = classify_jobs_file(name, data)
        folder = self.data_dir / "inbox" / kind
        dest = _stamped(folder, name, now or datetime.now(UTC))
        dest.write_bytes(data)
        links = None
        if kind == "links":
            text = data.decode("utf-8-sig", errors="replace")
            links = sum(
                1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")
            )
        return AddedJobsFile(kind=kind, dest=dest, links=links)

    def pick_contractor_file(
        self, name: str, data: bytes | None = None, path: Path | None = None
    ) -> ContractorFileInfo:
        """Check a contractor file now (so mistakes show at once) and keep it for the next
        run. From the native picker, `path` is used where it is; an upload (`data`) is
        saved under data/contractors/uploads/ first."""
        if Path(name).suffix.lower() not in CONTRACTOR_SUFFIXES:
            raise AppError(f"{name}: choose the contractor workbook (.xlsx) or a .csv file")
        if path is None:
            if data is None:
                raise AppError("No file was given.")
            path = _stamped(self.data_dir / "contractors" / "uploads", name, datetime.now(UTC))
            path.write_bytes(data)
        try:
            rows = read_contractor_file(
                path,
                config_dir=self.settings.config_dir,
                lock_timeout=self.settings.storage.lock_timeout_seconds,
            )
        except Exception as exc:  # shown to the person; holds no contractor data
            raise AppError(str(exc)) from exc
        self.contractor_file = path
        return ContractorFileInfo(path=path, contractors=len(rows))

    def contractor_count(self) -> int:
        contractors = self._loader().contractors
        return len(contractors.list_all()) if contractors.exists() else 0

    # ---- the run ----

    def _provider(self) -> LLMProvider:
        if self._provider_factory is not None:
            return self._provider_factory()
        from jobmatch.llm.gemini import GeminiProvider
        from jobmatch.llm.provider import LlmConfigError

        try:
            return GeminiProvider.from_settings(self.llm)
        except LlmConfigError as exc:
            raise AppError("Add your Gemini key in Settings first.") from exc

    def run(
        self,
        *,
        echo: Callable[[str], None],
        confirm: Callable[[RunCheck], bool],
        progress: Callable[[float, str], None] | None = None,
    ) -> RunOnceReport:
        """The "Find matches" button: load the picked contractor file (if any), import new
        jobs from the inbox, judge new jobs with Gemini, store shortlists. Blocking: the UI
        runs it in a worker thread. The picked contractor file is cleared once loaded."""
        report = run_once(
            self.settings,
            provider_factory=self._provider,
            contractor_file=self.contractor_file,
            confirm=confirm,
            echo=echo,
            progress=progress,
        )
        self.contractor_file = None
        return report

    def quota(self) -> Quota:
        today = UsageLedger(self._loader().llm_usage).today()
        return Quota(requests=today.requests, rpd=self.llm.limits.rpd, tokens=today.tokens)

    # ---- results and decisions ----

    def results(self) -> list[JobResult]:
        """Every active job with its current shortlist and the latest decision on each
        match. Jobs with fits first, then those with a partial to check, then no match,
        then not judged yet; newest first within each group."""
        loader = self._loader()
        jobs = [j for j in loader.jobs.list_all() if j.status in ACTIVE_STATUSES]
        profiles: dict[str, ContractorProfile] = (
            {p.talent_id: p for p in loader.contractors.list_all()}
            if loader.contractors.exists()
            else {}
        )
        keys = current_judge_keys(self.settings, loader)
        current = loader.llm_matches.current(keys)
        decisions = loader.reviews.latest_by_match()
        out: list[JobResult] = []
        for job in jobs:
            matches = current.get(job.job_id)
            if matches is None:
                outcome: JobOutcome = "not_judged"
            elif not matches:
                outcome = "no_match"
            elif any(m.verdict is LlmVerdict.FIT for m in matches):
                outcome = "fits"
            else:
                outcome = "check_partial"
            views = [
                MatchView(
                    match=m,
                    name=profiles[m.talent_id].talent_name if m.talent_id in profiles else None,
                    headline=(
                        profiles[m.talent_id].standardized_headline
                        if m.talent_id in profiles
                        else None
                    ),
                    decision=_current_decision(decisions.get(m.match_id)),
                )
                for m in matches or []
            ]
            out.append(JobResult(job=job, outcome=outcome, matches=views))
        order = {"fits": 0, "check_partial": 1, "no_match": 2, "not_judged": 3}
        out.sort(key=lambda r: r.job.first_seen_at, reverse=True)
        out.sort(key=lambda r: order[r.outcome])
        return out

    def decide(
        self,
        match_id: str,
        decision: Decision,
        reason: ReasonCode | None = None,
        notes: str | None = None,
    ) -> Review:
        """Store one decision in reviews.csv (append-only; the latest one counts)."""
        if decision is Decision.REJECTED and reason is None:
            raise AppError("Pick a reason for the rejection.")
        review = Review(
            match_id=match_id,
            decision=decision,
            reason_code=reason,
            notes=(notes or "").strip() or None,
            reviewer=self.reviewer(),
            reviewed_at=datetime.now(UTC),
        )
        self._loader().reviews.append(review)
        return review

    def clear_decision(self, match_id: str) -> None:
        """Reset a match to undecided. reviews.csv is append-only (it is the evaluation
        record), so this adds a "cleared" row instead of deleting anything."""
        review = Review(
            match_id=match_id,
            decision=Decision.CLEARED,
            reviewer=self.reviewer(),
            reviewed_at=datetime.now(UTC),
        )
        self._loader().reviews.append(review)

    def contractor_fields(self, talent_id: str) -> list[tuple[str, list[str]]]:
        """One contractor's stored record as (column name, values), in the contractor list's
        column order (the CSV columns): list fields as their items, other fields as text,
        empty fields as []. For the read-only Profile pop-up. Holds personal data: shown on
        screen only."""
        contractors = self._loader().contractors
        if not contractors.exists():
            raise AppError("There is no contractor list yet.")
        profile = next((p for p in contractors.list_all() if p.talent_id == talent_id), None)
        if profile is None:
            raise AppError(f"{talent_id} is not in the contractor list any more.")
        fields: list[tuple[str, list[str]]] = []
        for column in ContractorProfile.model_fields:
            value = getattr(profile, column)
            if isinstance(value, list):
                fields.append((column, [str(v) for v in value]))
            else:
                text = to_cell(value).strip()
                fields.append((column, [text] if text else []))
        return fields

    def export(self) -> ExportResult:
        return export_current(self.settings)
