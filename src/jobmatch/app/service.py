"""Everything the desktop app does, without any UI code (so it is tested directly).

The screens (app/ui.py) only call this class. It works on one Settings object whose data
paths point at the app's data root, and on the app's .env file for the Gemini key and the
reviewer name. Nothing here prints or logs contractor names or the key.
"""

import os
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from jobmatch import secrets_file
from jobmatch.backup import (
    BackupError,
    BackupInfo,
    backup_name,
    check_backup,
    current_counts,
    export_data,
    replace_data,
)
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
    RemovedJob,
    Review,
    TalentAvailability,
)
from jobmatch.shortlists import is_available, latest_toggles, pair_states, sync_rounds
from jobmatch.sources.contractor_upsert import ContractorFileError, read_contractor_file
from jobmatch.sources.job_csv import (
    FORM_MARK,
    JOB_CSV_SUFFIX,
    TEMPLATE_ROWS,
    JobCsvError,
    JobEntry,
    read_job_csv,
    write_job_csv,
)
from jobmatch.sources.resumes import (
    RESUME_SUFFIX,
    match_ids,
    read_contractor_folder,
    resumes_dir,
    stored_resume,
)
from jobmatch.sources.saved_files import SAVED_SUFFIXES
from jobmatch.storage.csv_store import to_cell
from jobmatch.storage.loader import create_loader
from jobmatch.storage.repositories import DataLoader

REVIEWER_ENV = "JOBMATCH_REVIEWER"
HIRE_REVIEWER = "hire"  # the availability row a hire writes (user decisions 2026-11-10)
HIRE_UNDONE_REVIEWER = "hire undone"
DEFAULT_REVIEWER = "reviewer"
JOB_SUFFIXES = (".txt", *SAVED_SUFFIXES, JOB_CSV_SUFFIX)
CONTRACTOR_SUFFIXES = (".xlsx", ".csv")
UPLOAD_SUFFIXES = (*CONTRACTOR_SUFFIXES, RESUME_SUFFIX)  # browser mode: file plus résumés

JobKind = Literal["links", "manual"]
JobOutcome = Literal["fits", "check_partial", "no_match", "not_judged"]


class AppError(Exception):
    """A problem to show to the person, in plain words."""


class PendingEntry(BaseModel):
    """A job entered with the form, saved and waiting for the next Ingest / Find matches."""

    name: str  # its file in data/inbox/manual/
    title: str
    company: str


class AddedJobsFile(BaseModel):
    kind: JobKind  # links: one URL per line; manual: a pasted or saved posting, jobs CSV
    dest: Path
    jobs: int | None = None  # a jobs CSV: rows that will go in
    problems: list[str] = []  # a jobs CSV: rows that will not, and why
    links: int | None = None  # number of URLs, for a links file


class ContractorFileInfo(BaseModel):
    """What the next run will load. Shown on screen only (`unmatched` file names may hold
    a person's name)."""

    path: Path | None  # the contractor file, if one is picked
    contractors: int
    resumes: int = 0  # PDFs named by a known talent ID
    unmatched: list[str] = []  # PDF names that are not a talent ID


class MatchView(BaseModel):
    match: LlmMatch
    name: str | None
    headline: str | None
    decision: Review | None  # latest decision, if any
    unavailable: bool = False  # not available for new projects (Talent screen)
    has_resume: bool = False  # a résumé is stored for them (Résumé button)


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


class TalentJob(BaseModel):
    """One job a contractor was approved for, on the Talent screen (read-only)."""

    job_id: str
    title: str
    company: str | None
    location: str | None
    status: ApplicationStatus
    removed: bool  # application rejected or ghosted
    job_open: bool  # False: the job is completed or unavailable (or deleted)
    deleted: bool = False  # the job was deleted from the app (Jobs screen)


class TalentOverview(BaseModel):
    """One contractor on the Talent screen. Shown on screen only (personal data)."""

    talent_id: str
    name: str
    headline: str | None
    available: bool  # what matching uses: the reviewer's toggle, else the profile
    profile_open: bool  # the contractor's own answer (profile `contract`)
    toggled: TalentAvailability | None  # the reviewer's latest toggle, if any
    jobs: list[TalentJob] = []  # approved for, best known first
    has_resume: bool = False


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
        raise AppError(f"{name}: choose a jobs .csv, or a .txt, .html or .pdf file")
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


def _entry_problem(exc: ValueError) -> str:
    """A form or row problem in plain words: "job_title: is empty" -> "Job title is empty"."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)
    parts = []
    for e in errors(include_input=False, include_url=False):
        field = ".".join(str(p) for p in e["loc"]).replace("_", " ").capitalize()
        parts.append(f"{field}: {e['msg'].removeprefix('Value error, ')}")
    return "; ".join(parts)


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
        self.resumes: dict[str, Path] = {}  # picked with it: {file stem: PDF}
        self._picked_ids: list[str] = []  # talent IDs in the picked file
        self._unpacked: Path | None = None  # a checked backup, waiting for confirmation

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
        if Path(name).suffix.lower() == JOB_CSV_SUFFIX:
            try:
                rows = read_job_csv(
                    dest, now or datetime.now(UTC), full_time_hours=self._full_time_hours()
                )
            except JobCsvError as exc:
                dest.unlink()  # our own copy, just made: nothing to keep
                raise AppError(str(exc).replace(dest.name, name)) from exc
            problems = [f"Row {r.row}: {r.error}" for r in rows if r.error]
            return AddedJobsFile(
                kind=kind, dest=dest, jobs=sum(r.job is not None for r in rows), problems=problems
            )
        links = None
        if kind == "links":
            text = data.decode("utf-8-sig", errors="replace")
            links = sum(
                1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")
            )
        return AddedJobsFile(kind=kind, dest=dest, links=links)

    # ---- export and import of all data (Settings; user decisions 2026-11-10) ----

    def _contractors_rel(self) -> Path:
        try:
            return self.settings.paths.contractors_csv.relative_to(self.data_dir)
        except ValueError:
            return Path("contractors") / "contractors.csv"

    def data_counts(self) -> tuple[int, int]:
        """(jobs, contractors) stored now."""
        return current_counts(self.data_dir, self._contractors_rel())

    def export_data(self, password: str, dest: Path | None = None) -> tuple[BackupInfo, Path]:
        """Write an encrypted backup of all data to `dest` (default: data/backups/exports/,
        which no export includes). The Gemini key and the reviewer name are never in it."""
        now = datetime.now(UTC)
        dest = dest or self.data_dir / "backups" / "exports" / backup_name(now)
        try:
            info = export_data(
                self.data_dir, dest, password, contractors_rel=self._contractors_rel(),
                lock_timeout=self.settings.storage.lock_timeout_seconds, now=now,
            )  # fmt: skip
        except BackupError as exc:
            raise AppError(str(exc)) from exc
        return info, dest

    def check_import(self, path: Path, password: str) -> BackupInfo:
        """Check a backup completely and unpack it beside the data (nothing replaced yet).
        import_checked() then puts it in place; discard_import() drops it."""
        self.discard_import()
        try:
            info, unpacked = check_backup(
                path, password, work_dir=self.data_dir / "backups",
                contractors_rel=self._contractors_rel(),
                lock_timeout=self.settings.storage.lock_timeout_seconds,
            )  # fmt: skip
        except BackupError as exc:
            raise AppError(str(exc)) from exc
        self._unpacked = unpacked
        return info

    def import_checked(self) -> Path:
        """Replace all data with the checked backup. The current data is moved (never
        deleted) into data/backups/before-import-<time>/; returns that folder."""
        if self._unpacked is None or not self._unpacked.is_dir():
            raise AppError("Check the backup again before importing it.")
        try:
            kept = replace_data(
                self.data_dir, self._unpacked,
                lock_timeout=self.settings.storage.lock_timeout_seconds, now=datetime.now(UTC),
            )  # fmt: skip
        except BackupError as exc:
            raise AppError(str(exc)) from exc
        self._unpacked = None
        self.clear_contractor_picks()
        return kept

    def discard_import(self) -> None:
        if self._unpacked is not None:
            shutil.rmtree(self._unpacked, ignore_errors=True)  # our own unpacked copy
            self._unpacked = None

    # ---- jobs entered with the form (user decisions 2026-11-10) ----

    def _full_time_hours(self) -> float:
        return self.settings.normalize.full_time_hours_per_week

    def _manual_inbox(self) -> Path:
        return self.data_dir / "inbox" / "manual"

    def add_job_entry(self, fields: dict[str, str], now: datetime | None = None) -> JobEntry:
        """Save one job from the "Add a job by hand" form, as a one-row jobs CSV in the
        inbox; the next Ingest or Find matches stores it. Checked now (the link, the title
        and the date), so mistakes show in the form."""
        now = now or datetime.now(UTC)
        try:
            entry = JobEntry.from_cells(dict(fields))
            entry.to_job(now, full_time_hours=self._full_time_hours())
        except ValueError as exc:
            raise AppError(_entry_problem(exc)) from exc
        slug = re.sub(r"[^a-z0-9]+", "-", entry.job_title.lower()).strip("-")[:40] or "job"
        dest = _stamped(self._manual_inbox(), f"entered{FORM_MARK}{slug}.csv", now)
        write_job_csv(dest, [entry])
        return entry

    def pending_entries(self) -> list[PendingEntry]:
        """Jobs entered with the form that are not stored yet, oldest first."""
        folder = self._manual_inbox()
        if not folder.is_dir():
            return []
        out = []
        for path in sorted(folder.glob(f"*{FORM_MARK}*{JOB_CSV_SUFFIX}")):
            try:
                rows = read_job_csv(path, datetime.now(UTC))
            except JobCsvError:
                continue
            for r in rows:
                if r.job is not None:
                    out.append(
                        PendingEntry(name=path.name, title=r.job.title_raw,
                                     company=r.job.company_raw or "")
                    )  # fmt: skip
        return out

    def remove_entry(self, name: str) -> None:
        """Remove a form entry that is not stored yet (the screen asks first)."""
        if name not in {e.name for e in self.pending_entries()}:
            raise AppError("That entry is already stored or removed.")
        (self._manual_inbox() / name).unlink()

    def job_template(self) -> Path:
        """A jobs CSV template with the header and two example rows (English, French)."""
        dest = self.data_dir / "templates" / "jobs-template.csv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_job_csv(dest, TEMPLATE_ROWS)
        return dest

    def pick_contractor_folder(self, folder: Path) -> ContractorFileInfo:
        """The contractor folder (native picker): one .xlsx/.csv plus an optional resume/
        folder of <talent_id>.pdf (sources/resumes.py). Checked now, loaded on the next
        run."""
        try:
            picked = read_contractor_folder(folder)
        except ContractorFileError as exc:
            raise AppError(str(exc)) from exc
        self.resumes = {}
        self._check_contractor_file(picked.contractor_file)
        self.resumes = dict(picked.resumes)
        return self._picked()

    def pick_contractor_file(
        self, name: str, data: bytes | None = None, path: Path | None = None
    ) -> ContractorFileInfo:
        """One picked file: the contractor file, or a résumé PDF named by talent ID (the
        browser upload takes the folder's files one by one). Checked now (so mistakes show
        at once) and kept for the next run. From the native picker, `path` is used where it
        is; an upload (`data`) is saved under data/contractors/uploads/ first."""
        suffix = Path(name).suffix.lower()
        if suffix not in UPLOAD_SUFFIXES:
            raise AppError(
                f"{name}: choose the contractor workbook (.xlsx) or a .csv file, and résumés "
                "as .pdf"
            )
        if path is None:
            if data is None:
                raise AppError("No file was given.")
            path = _stamped(self.data_dir / "contractors" / "uploads", name, datetime.now(UTC))
            path.write_bytes(data)
        if suffix == RESUME_SUFFIX:
            self.resumes[Path(name).stem.strip()] = path
        else:
            self._check_contractor_file(path)
        return self._picked()

    def clear_contractor_picks(self) -> None:
        """Forget the picked contractor file and résumés (a new folder was picked)."""
        self.contractor_file = None
        self.resumes = {}
        self._picked_ids = []

    def _check_contractor_file(self, path: Path) -> None:
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
        self._picked_ids = [r.talent_id for r in rows]

    def _picked(self) -> ContractorFileInfo:
        contractors = self._loader().contractors
        known = [c.talent_id for c in contractors.list_all()] if contractors.exists() else []
        matched, unmatched = match_ids(self.resumes, [*known, *self._picked_ids])
        return ContractorFileInfo(
            path=self.contractor_file,
            contractors=len(self._picked_ids),
            resumes=len(matched),
            unmatched=unmatched,
        )

    def resume_path(self, talent_id: str) -> Path | None:
        """The stored résumé of a contractor in the list, or None."""
        contractors = self._loader().contractors
        known = [c.talent_id for c in contractors.list_all()] if contractors.exists() else []
        return stored_resume(self.data_dir, talent_id, known)

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
        match: bool = True,
    ) -> RunOnceReport:
        """The "Find matches" button: load the picked contractor file (if any), import new
        jobs from the inbox, judge new jobs with Gemini, store shortlists. Blocking: the UI
        runs it in a worker thread. The picked contractor file is cleared once loaded.
        match=False is the "Ingest" button: store contractors, résumés and jobs only (no
        Gemini call), so the reviewer can check them before matching."""
        report = run_once(
            self.settings,
            provider_factory=self._provider,
            contractor_file=self.contractor_file,
            resumes=self.resumes,
            match=match,
            confirm=confirm,
            echo=echo,
            progress=progress,
        )
        self.contractor_file = None
        self.resumes = {}
        self._picked_ids = []
        return report

    def quota(self) -> Quota:
        # The screening model: one call per job, so its quota decides how many jobs fit today.
        today = UsageLedger(self._loader().llm_usage).today(model=self.llm.model)
        return Quota(requests=today.requests, rpd=self.llm.limits.rpd, tokens=today.tokens)

    # ---- results and decisions ----

    def _profiles(self, loader: DataLoader) -> dict[str, ContractorProfile]:
        contractors = loader.contractors
        return {p.talent_id: p for p in contractors.list_all()} if contractors.exists() else {}

    def _with_resume(self) -> set[str]:
        """Talent IDs with a stored résumé."""
        folder = resumes_dir(self.data_dir)
        return {f.stem for f in folder.glob(f"*{RESUME_SUFFIX}")} if folder.is_dir() else set()

    def results(self) -> list[JobResult]:
        """Every active job with its recommendations (llm/rounds.py) and the latest decision
        on each. Jobs with fits first, then those with a partial to check, then no match,
        then not judged yet; newest first within each group. Brings the rounds up to date
        first (no API call)."""
        loader = self._loader()
        pictures = sync_rounds(self.settings, loader)
        profiles = self._profiles(loader)
        decisions = latest_reviews(loader.llm_matches.list_all(), loader.reviews.latest_by_match())
        with_resume = self._with_resume()

        def view(m: LlmMatch) -> MatchView:
            profile = profiles.get(m.talent_id)
            return MatchView(
                match=m,
                name=profile.talent_name if profile else None,
                headline=profile.standardized_headline if profile else None,
                decision=_current_decision(decisions.get((m.job_id, m.talent_id))),
                unavailable=m.talent_id in pictures[m.job_id].unavailable,
                has_resume=m.talent_id in with_resume,
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

    # ---- the Talent screen ----

    def talent_overview(self) -> list[TalentOverview]:
        """Every contractor in the contractor list, with their availability and the jobs
        they were approved for: available A–Z by name, then not available A–Z. Read-only
        except for the availability toggle (profiles change only through the intake form
        and the next contractor import)."""
        loader = self._loader()
        profiles = list(self._profiles(loader).values())
        latest = latest_toggles(loader)
        states = pair_states(loader)
        applications = latest_applications(loader.applications.list_all())
        jobs = {j.job_id: j for j in loader.jobs.list_all()}
        gone: dict[str, list[RemovedJob]] = {}
        for r in loader.removed_jobs.list_all():
            gone.setdefault(r.talent_id, []).append(r)
        with_resume = self._with_resume()
        out: list[TalentOverview] = []
        for p in profiles:
            toggle = latest.get(p.talent_id)
            approved: list[TalentJob] = []
            for (job_id, talent_id), state in states.items():
                job = jobs.get(job_id)
                if talent_id != p.talent_id or state not in ("approved", "removed") or not job:
                    continue
                application = applications.get((job_id, talent_id))
                approved.append(
                    TalentJob(
                        job_id=job_id,
                        title=job.title_norm or job.title_raw,
                        company=job.company_raw,
                        location=job.location_raw,
                        status=application.status
                        if application
                        else ApplicationStatus.NOT_REFERRED,
                        removed=state == "removed",
                        job_open=job.availability is JobAvailability.AVAILABLE,
                    )
                )
            listed = {t.job_id for t in approved}
            for r in gone.get(p.talent_id, []):
                if r.job_id in listed:
                    continue
                approved.append(
                    TalentJob(
                        job_id=r.job_id,
                        title=r.title,
                        company=r.company,
                        location=r.location,
                        status=r.status,
                        removed=r.status is ApplicationStatus.REJECTED,
                        job_open=False,
                        deleted=True,
                    )
                )
            approved.sort(key=lambda t: (t.removed, not t.job_open, t.title.casefold()))
            out.append(
                TalentOverview(
                    talent_id=p.talent_id,
                    name=p.talent_name,
                    headline=p.standardized_headline,
                    available=is_available(p, toggle.available if toggle else None),
                    profile_open=p.contract,
                    toggled=toggle,
                    jobs=approved,
                    has_resume=p.talent_id in with_resume,
                )
            )
        out.sort(key=lambda t: (not t.available, t.name.casefold(), t.talent_id))
        return out

    def set_talent_availability(
        self, talent_id: str, available: bool, note: str = "", now: datetime | None = None
    ) -> TalentAvailability:
        """The reviewer's "Available for matching" toggle, after checking with the
        contractor. Overrides the profile's own answer either way. Appended to
        talent_availability.csv (the latest counts). Set automatically only by a hire
        (set_application)."""
        loader = self._loader()
        if talent_id not in self._profiles(loader):
            raise AppError(f"{talent_id} is not in the contractor list any more.")
        row = TalentAvailability(
            talent_id=talent_id,
            available=available,
            note=" ".join(note.split()) or None,
            reviewer=self.reviewer(),
            changed_at=now or datetime.now(UTC),
        )
        loader.talent_availability.add_many([row])
        return row

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
        the job completed and the contractor not available (user decisions 2026-11-10);
        moving away from "hired" restores their availability from before the hire, unless
        a reviewer changed it since. Appended to applications.csv (the latest counts)."""
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
        history = self._history(loader, job_id, talent_id)
        was_hired = bool(history) and history[-1].status is ApplicationStatus.HIRED
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
        title = job.title_norm or job.title_raw
        if status is ApplicationStatus.HIRED:
            if job.availability is JobAvailability.AVAILABLE:
                self.set_job_availability(job_id, "completed", now)
            if not was_hired:
                loader.talent_availability.add_many(
                    [
                        TalentAvailability(
                            talent_id=talent_id,
                            available=False,
                            note=f"Hired for {title} ({job_id})",
                            reviewer=HIRE_REVIEWER,
                            changed_at=now,
                        )
                    ]
                )
        elif was_hired:
            self._undo_hire_availability(loader, job_id, talent_id, title, now)

    def _undo_hire_availability(
        self, loader: DataLoader, job_id: str, talent_id: str, title: str, now: datetime
    ) -> None:
        """The hire was undone: availability goes back to what it was before it, unless a
        reviewer changed it since (their choice stands)."""
        rows = sorted(
            (t for t in loader.talent_availability.list_all() if t.talent_id == talent_id),
            key=lambda t: t.changed_at,
        )
        hire_note = f"({job_id})"
        if (
            not rows
            or rows[-1].reviewer != HIRE_REVIEWER
            or not (rows[-1].note or "").endswith(hire_note)
        ):
            return
        profile = self._profiles(loader).get(talent_id)
        before = rows[-2].available if len(rows) > 1 else bool(profile and profile.contract)
        loader.talent_availability.add_many(
            [
                TalentAvailability(
                    talent_id=talent_id,
                    available=before,
                    note=f"Hire undone for {title} ({job_id})",
                    reviewer=HIRE_UNDONE_REVIEWER,
                    changed_at=now,
                )
            ]
        )

    # ---- deletions (the screens ask for confirmation first) ----

    def delete_job(self, job_id: str, now: datetime | None = None) -> int:
        """Delete a completed or unavailable job from the app (user decisions 2026-11-10):
        its row in jobs.csv, its scans, rounds and application statuses. The contractors
        approved for it keep it on the Talent screen (removed_jobs.csv: title, company,
        location, their status). Shortlist rows and decisions stay (evaluation data, IDs
        only). Every file is backed up before it is rewritten. Returns the contractors
        who keep it on their list."""
        loader = self._loader()
        job = loader.jobs.get(job_id)
        if job is None:
            raise AppError("That job is not in the job list any more.")
        if job.availability is JobAvailability.AVAILABLE:
            raise AppError("Only a completed or unavailable job can be deleted.")
        now = now or datetime.now(UTC)
        applications = latest_applications(loader.applications.list_all())
        keep = [
            RemovedJob(
                job_id=job_id,
                talent_id=talent_id,
                title=job.title_norm or job.title_raw,
                company=job.company_raw,
                location=job.location_raw,
                status=a.status if (a := applications.get((j, talent_id))) else (
                    ApplicationStatus.REJECTED if state == "removed"
                    else ApplicationStatus.NOT_REFERRED
                ),
                removed_at=now,
                removed_by=self.reviewer(),
            )
            for (j, talent_id), state in pair_states(loader).items()
            if j == job_id and state in ("approved", "removed")
        ]  # fmt: skip
        if keep:
            loader.removed_jobs.add_many(keep)  # first: an interrupted delete loses nothing
        loader.job_scans.remove_where(lambda r: r.job_id == job_id)
        loader.job_rounds.remove_where(lambda r: r.job_id == job_id)
        loader.applications.remove_where(lambda r: r.job_id == job_id)
        loader.jobs.remove(job_id)
        return len(keep)

    def delete_talent(self, talent_id: str) -> None:
        """Delete a contractor and everything about them (user decisions 2026-11-10): their
        row in the app's contractor list, résumé (and old copies), availability, change
        marks, scans, round slots, shortlist rows, the decisions on those rows, application
        statuses and removed-job rows. Only when they are not available for matching. Each
        CSV is backed up before it is rewritten (CLAUDE.md); the picked contractor folder is
        never touched, and a later import adds them back as new."""
        loader = self._loader()
        profile = self._profiles(loader).get(talent_id)
        if profile is None:
            raise AppError(f"{talent_id} is not in the contractor list any more.")
        toggle = latest_toggles(loader).get(talent_id)
        if is_available(profile, toggle.available if toggle else None):
            raise AppError("Mark them not available (and save) before deleting them.")
        matches = {m.match_id for m in loader.llm_matches.list_all() if m.talent_id == talent_id}
        mine = lambda r: r.talent_id == talent_id  # noqa: E731
        loader.reviews.remove_where(lambda r: r.match_id in matches)
        loader.llm_matches.remove_where(mine)
        loader.job_scans.remove_where(mine)
        loader.job_rounds.remove_where(mine)
        loader.applications.remove_where(mine)
        loader.removed_jobs.remove_where(mine)
        loader.contractor_changes.remove_where(mine)
        loader.talent_availability.remove_where(mine)
        folder = resumes_dir(self.data_dir)
        (folder / f"{talent_id}{RESUME_SUFFIX}").unlink(missing_ok=True)
        old = re.compile(rf"{re.escape(talent_id)}-\d{{8}}T\d{{6}}Z{RESUME_SUFFIX}")
        if (folder / "old").is_dir():
            for pdf in (folder / "old").iterdir():
                if old.fullmatch(pdf.name):
                    pdf.unlink()
        loader.contractors.remove_where(mine)  # last: the delete can be run again

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
