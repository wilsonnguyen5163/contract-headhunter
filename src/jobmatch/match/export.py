"""A spreadsheet of the current matches, for people to judge the matcher before the review UI.

One row per (job, contractor): shortlisted ("shortlist") and flagged ("flagged") together,
the newest row of each pair only. It holds contractor names, so it is a local file for
people: never attach it to logs or send it anywhere. Formula-like cells are neutralized
by export_csv, because posting text is untrusted.
"""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from jobmatch.schemas import ContractorProfile, Job, Match
from jobmatch.storage.csv_store import export_csv


class MatchExportRow(BaseModel):
    job_title: str
    company: str | None
    city: str | None
    job_url: str
    list: str  # "shortlist" or "flagged"
    rank: int
    band: str
    score: float
    cosine: float
    title_cosine: float | None
    matched_title: str | None
    skill_overlap: float | None
    contractor_id: str
    contractor_name: str | None
    headline: str | None
    contractor_location: str | None
    rate_expectation: str | None
    weekly_hours: float | None
    matched_skills: list[str]
    missing_must_have: list[str]
    gates_failed: list[str]
    flags: list[str]
    match_id: str
    scored_at: datetime


def latest(matches: Iterable[Match]) -> list[Match]:
    """The newest row per (job, contractor)."""
    newest: dict[tuple[str, str], Match] = {}
    for m in matches:
        key = (m.job_id, m.contractor_id)
        if key not in newest or m.created_at > newest[key].created_at:
            newest[key] = m
    return list(newest.values())


def build_rows(
    shortlisted: Iterable[Match],
    flagged: Iterable[Match],
    jobs: dict[str, Job],
    profiles: dict[str, ContractorProfile],
) -> list[MatchExportRow]:
    tagged = [(m, "shortlist") for m in shortlisted] + [(m, "flagged") for m in flagged]
    current = {m.match_id for m in latest(m for m, _ in tagged)}
    rows: list[MatchExportRow] = []
    for m, which in tagged:
        job = jobs.get(m.job_id)
        if m.match_id not in current or job is None:
            continue
        p = profiles.get(m.contractor_id)
        rows.append(
            MatchExportRow(
                job_title=job.title_norm or job.title_raw,
                company=job.company_raw,
                city=job.city,
                job_url=job.url,
                list=which,
                rank=m.rank,
                band=m.band.value,
                score=m.score,
                cosine=m.cosine,
                title_cosine=m.title_cosine,
                matched_title=m.matched_title,
                skill_overlap=m.skill_overlap,
                contractor_id=m.contractor_id,
                contractor_name=p.talent_name if p else None,
                headline=p.standardized_headline if p else None,
                contractor_location=p.location if p else None,
                rate_expectation=p.rate_expectation if p else None,
                weekly_hours=p.weekly_hours if p else None,
                matched_skills=m.matched_skills,
                missing_must_have=m.missing_must_have,
                gates_failed=m.gates_failed,
                flags=m.flags,
                match_id=m.match_id,
                scored_at=m.created_at,
            )
        )
    rows.sort(key=lambda r: (r.job_title, r.job_url, r.rank))
    return rows


def write_export(rows: list[MatchExportRow], dest: Path) -> int:
    return export_csv(MatchExportRow, rows, dest)
