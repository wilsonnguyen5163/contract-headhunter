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
from jobmatch.llm.payload import ACTIVE_STATUSES, is_active
from jobmatch.llm.provider import LLMProvider
from jobmatch.llm.rounds import latest_applications, latest_reviews, rank
from jobmatch.llm.usage import RunCheck, UsageLedger
from jobmatch.pipeline import RunOnceReport, run_once
from jobmatch.schemas import (
    Application,
    ApplicationStatus,
    ContractorProfile,
    Decision,
    Job,
    JobAvailability,
    LlmMatch,
    LlmVerdict,
    ReasonCode,
    Review,
)
from jobmatch.shortlists import pair_states, sync_rounds
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
    rejected: list[MatchView] = []  # the reviewer's rejections, behind "Show rejected"


# The Jobs screen's four states:
#   matched  (1, green)  scanned, someone recommended (reviewed or not, hire in progress or not)
#   no_match (2, red)    scanned, nobody recommended; stays available for later runs
#   waiting  (3, yellow) contractors still to scan (a new or reopened job, new contractors)
#   inactive (4, grey)   marked completed or unavailable: never sent to Gemini
JobState = Literal["matched", "no_match", "waiting", "inactive"]
JobAction = Literal["completed", "unavailable", "reopen"]


class Candidate(BaseModel):
    """An approved contractor in a job's table on the Jobs screen."""

    talent_id: str
    name: str | None
    headline: str | None
    match: LlmMatch | None  # their newest row for the job (verdict, score)
    status: ApplicationStatus  # latest; not_referred when never set
    removed: bool  # application rejected or ghosted: out of consideration (Undo restores)


class JobOverview(BaseModel):
    job: Job
    state: JobState
    summary: str  # "3 matches · best score 92", "best score 58", "no match", "" (not scanned)
    candidates: list[Candidate] = []  # approved for this job, best first


def job_summary(job: Job, matches: list[LlmMatch] | None) -> str:
    """The matching summary line on the Jobs screen. `matches` is the current shortlist,
    None when the job is not analyzed with the current contractor list."""
    if job.availability is JobAvailability.COMPLETED:
        return "completed"
    if job.availability is JobAvailability.UNAVAILABLE:
        return "unavailable"
    if matches is None:
        return ""  # the screen's state label already says "Not scanned yet"
    if not matches:
        return "no match"
    scores = [m.fit_score for m in matches if m.fit_score is not None]
    best = f"best score {max(scores)}" if scores else "no score"
    fits = sum(1 for m in matches if m.verdict is LlmVerdict.FIT)
    if not fits:
        return best  # the single best partial
    return f"{fits} match{'es' if fits != 1 else ''} · {best}"


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
        contractors = self._loader().contractors
        try:
            rows = read_contractor_file(
                path,
                config_dir=self.settings.config_dir,
                lock_timeout=self.settings.storage.lock_timeout_seconds,
                existing=contractors.list_all() if contractors.exists() else [],
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
        # The screening model: one call per job, so its quota decides how many jobs fit today.
        today = UsageLedger(self._loader().llm_usage).today(model=self.llm.model)
        return Quota(requests=today.requests, rpd=self.llm.limits.rpd, tokens=today.tokens)

    # ---- results and decisions ----

    def _profiles(self, loader: DataLoader) -> dict[str, ContractorProfile]:
        contractors = loader.contractors
        return {p.talent_id: p for p in contractors.list_all()} if contractors.exists() else {}

    def results(self) -> list[JobResult]:
        """Every active job with its recommendations (llm/rounds.py) and the latest decision
        on each. Jobs with fits first, then those with a partial to check, then no match,
        then not judged yet; newest first within each group. Brings the rounds up to date
        first (no API call)."""
        loader = self._loader()
        pictures = sync_rounds(self.settings, loader)
        profiles = self._profiles(loader)
        decisions = latest_reviews(loader.llm_matches.list_all(), loader.reviews.latest_by_match())

        def view(m: LlmMatch) -> MatchView:
            profile = profiles.get(m.talent_id)
            return MatchView(
                match=m,
                name=profile.talent_name if profile else None,
                headline=profile.standardized_headline if profile else None,
                decision=_current_decision(decisions.get((m.job_id, m.talent_id))),
            )

        out: list[JobResult] = []
        for job in loader.jobs.list_all():
            if not is_active(job):
                continue
            picture = pictures[job.job_id]
            shown = picture.shortlist.shown
            if not shown and picture.to_scan:
                outcome: JobOutcome = "not_judged"
            elif not shown:
                outcome = "no_match"
            elif any(m.verdict is LlmVerdict.FIT for m in shown):
                outcome = "fits"
            else:
                outcome = "check_partial"
            out.append(
                JobResult(
                    job=job,
                    outcome=outcome,
                    matches=[view(m) for m in shown],
                    rejected=[view(m) for m in picture.shortlist.rejected],
                )
            )
        order = {"fits": 0, "check_partial": 1, "no_match": 2, "not_judged": 3}
        out.sort(key=lambda r: r.job.first_seen_at, reverse=True)
        out.sort(key=lambda r: order[r.outcome])
        return out

    def job_result(self, job_id: str) -> JobResult | None:
        """One job's card on Results, fresh (after a reject or a reset); None when the job
        is no longer active."""
        return next((r for r in self.results() if r.job.job_id == job_id), None)

    # ---- the Jobs screen ----

    def jobs_overview(self) -> list[JobOverview]:
        """Every imported job with its state, matching summary and approved candidates: open
        jobs A–Z by title, then closed jobs A–Z. Jobs the import left out (not contract, not
        Canada, duplicates, stubs without details) are not listed: they were never
        candidates."""
        loader = self._loader()
        jobs = [
            j
            for j in loader.jobs.list_all()
            if j.status in ACTIVE_STATUSES or j.availability is not JobAvailability.AVAILABLE
        ]
        pictures = sync_rounds(self.settings, loader)
        profiles = self._profiles(loader)
        applications = latest_applications(loader.applications.list_all())
        out: list[JobOverview] = []
        for job in jobs:
            picture = pictures[job.job_id]
            shown = picture.shortlist.shown
            if not is_active(job):
                state: JobState = "inactive"
            elif picture.to_scan:
                state = "waiting"
            elif shown:
                state = "matched"
            else:
                state = "no_match"
            matches = shown if shown or not picture.to_scan else None
            candidates = []
            for talent_id, s in picture.states.items():
                if s not in ("approved", "removed"):
                    continue
                profile = profiles.get(talent_id)
                application = applications.get((job.job_id, talent_id))
                status = application.status if application else ApplicationStatus.NOT_REFERRED
                candidates.append(
                    Candidate(
                        talent_id=talent_id,
                        name=profile.talent_name if profile else None,
                        headline=profile.standardized_headline if profile else None,
                        match=picture.rows.get(talent_id),
                        status=status,
                        removed=s == "removed",
                    )
                )
            candidates.sort(key=lambda c: (c.removed, rank(c.match) if c.match else (True,)))
            out.append(
                JobOverview(
                    job=job, state=state, summary=job_summary(job, matches), candidates=candidates
                )
            )
        # Open jobs A–Z, then closed ones (completed or unavailable) A–Z at the bottom.
        out.sort(
            key=lambda o: (
                o.state == "inactive",
                (o.job.title_norm or o.job.title_raw).casefold(),
                o.job.job_id,
            )
        )
        return out

    def set_job_availability(
        self, job_id: str, action: JobAction, now: datetime | None = None
    ) -> Job:
        """The Jobs screen toggles. completed / unavailable: the job leaves Results and is
        never sent to Gemini. reopen (only from those two): available again; the next run sends
        only contractors not judged for it yet (shortlists.py). One atomic rewrite of jobs.csv,
        after a timestamped backup."""
        jobs = self._loader().jobs
        job = jobs.get(job_id)
        if job is None:
            raise AppError("That job is not in the job list any more.")
        now = now or datetime.now(UTC)
        inactive = job.availability is not JobAvailability.AVAILABLE
        if action == "reopen":
            if not inactive:
                raise AppError("Only a completed or unavailable job can be reopened.")
            update = {
                "availability": JobAvailability.AVAILABLE,
                "availability_changed_at": now,
                "reopened_at": now,
            }
        else:
            if inactive:
                raise AppError("This job is already marked completed or unavailable.")
            update = {"availability": JobAvailability(action), "availability_changed_at": now}
        changed = job.model_copy(update=update)
        jobs.replace([changed])
        return changed

    def _history(self, loader: DataLoader, job_id: str, talent_id: str) -> list[Application]:
        rows = [
            a
            for a in loader.applications.list_all()
            if a.job_id == job_id and a.talent_id == talent_id
        ]
        return sorted(rows, key=lambda a: a.updated_at)  # stable: file order on ties

    def set_application(
        self,
        job_id: str,
        talent_id: str,
        status: ApplicationStatus,
        now: datetime | None = None,
    ) -> None:
        """Save an approved candidate's application status (Jobs screen). "rejected"
        (rejection or ghosted) takes them out of consideration for this job; "hired" marks
        the job completed. Appended to applications.csv (the latest counts)."""
        loader = self._loader()
        job = loader.jobs.get(job_id)
        if job is None:
            raise AppError("That job is not in the job list any more.")
        state = pair_states(loader).get((job_id, talent_id))
        if state not in ("approved", "removed"):
            raise AppError("Only a candidate approved on Results has an application status.")
        if state == "removed":
            raise AppError("This candidate's application was rejected: use Undo first.")
        now = now or datetime.now(UTC)
        loader.applications.add_many(
            [
                Application(
                    job_id=job_id,
                    talent_id=talent_id,
                    status=status,
                    reviewer=self.reviewer(),
                    updated_at=now,
                )  # fmt: skip
            ]
        )
        if status is ApplicationStatus.HIRED and job.availability is JobAvailability.AVAILABLE:
            self.set_job_availability(job_id, "completed", now)

    def undo_application_rejection(
        self, job_id: str, talent_id: str, now: datetime | None = None
    ) -> ApplicationStatus:
        """Bring back a candidate whose application was rejected or ghosted (a misclick,
        usually): their status goes back to what it was before. Returns that status."""
        loader = self._loader()
        history = self._history(loader, job_id, talent_id)
        if not history or history[-1].status is not ApplicationStatus.REJECTED:
            raise AppError("This candidate's application is not rejected: nothing to undo.")
        before = next(
            (a.status for a in reversed(history) if a.status is not ApplicationStatus.REJECTED),
            ApplicationStatus.NOT_REFERRED,
        )
        loader.applications.add_many(
            [
                Application(
                    job_id=job_id,
                    talent_id=talent_id,
                    status=before,
                    reviewer=self.reviewer(),
                    updated_at=now or datetime.now(UTC),
                )  # fmt: skip
            ]
        )
        return before

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
