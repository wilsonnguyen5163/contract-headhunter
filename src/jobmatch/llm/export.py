"""The human-facing spreadsheet of v1 shortlists (export-llm-matches). One row per
shortlisted contractor; a job with no match, or not judged yet, gets one row saying so.
Cells are formula-neutralized by export_csv (posting text is untrusted). Holds contractor
names: the file stays local."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from jobmatch.schemas import ContractorProfile, Job, LlmMatch
from jobmatch.storage.csv_store import export_csv

if TYPE_CHECKING:
    from jobmatch.config import Settings


class LlmExportRow(BaseModel):
    job_title: str
    company: str | None = None
    location: str | None = None
    job_url: str
    job_id: str
    result: str  # "match", "no match", "not judged yet"
    rank: int | None = None
    verdict: str | None = None
    detail: str | None = None  # "full" or "reason only" (Gemini did not describe them)
    fit_score: int | None = None
    talent_id: str | None = None
    talent_name: str | None = None
    headline: str | None = None
    reason: str | None = None
    requirements_met: list[str] = []
    requirements_missing: list[str] = []
    red_flags: list[str] = []
    evidence_check: str | None = None  # v10: whether every "meets" row was found in the texts
    model: str | None = None
    match_id: str | None = None


def build_export_rows(
    current: Mapping[str, list[LlmMatch]],
    active_jobs: Sequence[Job],
    profiles: Mapping[str, ContractorProfile],
) -> list[LlmExportRow]:
    """current: job_id -> shortlist, for judged jobs only (CsvLlmMatchRepository.current)."""
    rows: list[LlmExportRow] = []
    for job in active_jobs:
        base = {
            "job_title": job.title_norm or job.title_raw,
            "company": job.company_raw,
            "location": job.location_raw,
            "job_url": job.url,
            "job_id": job.job_id,
        }
        if job.job_id not in current:
            rows.append(LlmExportRow(**base, result="not judged yet"))
            continue
        shortlist = current[job.job_id]
        if not shortlist:
            rows.append(LlmExportRow(**base, result="no match"))
            continue
        for m in shortlist:
            profile = profiles.get(m.talent_id)
            rows.append(
                LlmExportRow(
                    **base,
                    result="match",
                    rank=m.rank,
                    verdict=m.verdict.value,
                    detail="full" if m.detailed else "reason only",
                    fit_score=m.fit_score,
                    talent_id=m.talent_id,
                    talent_name=profile.talent_name if profile else None,
                    headline=profile.standardized_headline if profile else None,
                    reason=m.reason,
                    requirements_met=m.requirements_met,
                    requirements_missing=m.requirements_missing,
                    red_flags=m.red_flags,
                    evidence_check=(
                        "something could not be found in the profile or job and was left out"
                        if m.unverified_evidence
                        else "verified"
                        if m.detailed
                        else None
                    ),
                    model=m.model,
                    match_id=m.match_id,
                )
            )
    return rows


def write_llm_export(rows: list[LlmExportRow], dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    return export_csv(LlmExportRow, rows, dest)


class ExportResult(BaseModel):
    path: Path
    rows: int
    jobs: int


def export_current(settings: "Settings", now: datetime | None = None) -> ExportResult:
    """Write each active job's current shortlist to exports/llm-matches-<time>.csv. No API
    call. Used by export-llm-matches and the desktop app's Export button."""
    from jobmatch.llm.payload import is_active
    from jobmatch.shortlists import sync_rounds
    from jobmatch.storage.loader import create_loader

    loader = create_loader(settings)
    profiles = loader.contractors.list_all() if loader.contractors.exists() else []
    active = [j for j in loader.jobs.list_all() if is_active(j)]
    # What Results shows (llm/rounds.py), ranked 1.. in screen order; a job still to scan
    # with nothing shown yet is "not judged". Rejected candidates are left out.
    current = {}
    for job_id, picture in sync_rounds(settings, loader).items():
        shown = picture.shortlist.shown
        if shown or not picture.to_scan:
            current[job_id] = [m.model_copy(update={"rank": i}) for i, m in enumerate(shown, 1)]
    rows = build_export_rows(current, active, {p.talent_id: p for p in profiles})
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    dest = settings.paths.exports_dir / f"llm-matches-{stamp}.csv"
    return ExportResult(path=dest, rows=write_llm_export(rows, dest), jobs=len(active))
