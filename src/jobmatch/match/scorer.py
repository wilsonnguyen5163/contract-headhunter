"""Score one job against every contractor and pick the shortlist.

    score = weighted average of
        scaled_cosine   full job text vs full profile
        scaled_title    job title vs the contractor's closest title
        skill_overlap   job skills the contractor has / min(job's skill count, cap)
        field           share of the job's type of work (skill categories) the contractor covers

Cosines are stretched from [floor, ceiling] to [0, 1], one fixed scale for all jobs, so a job
nobody fits still looks weak. A part with nothing to compare (a job with no skills, no title
vectors) is left out and the others share its weight; so is the full text of a thin profile
(thin_profiles_skip_text), which has too little text to compare fairly. The band comes from
the score, one step lower when a must-have skill is missing and missing_must_have_lowers_band
is on. Every number is in settings.yaml (matching), to be tuned on reviewer data.
"""

from collections import Counter

import numpy as np
from pydantic import BaseModel, ConfigDict

from jobmatch.config import ConfidenceBands, MatchingSettings
from jobmatch.match.fields import field_note, field_overlap, industry_note
from jobmatch.match.gates import flags, is_thin, location_check, seniority_check
from jobmatch.schemas import Band, ContractorProfile, Job


class ContractorFacts(BaseModel):
    """What the scorer needs about one contractor, derived once per run."""

    model_config = ConfigDict(frozen=True)

    profile: ContractorProfile
    skills: frozenset[str]  # canonical taxonomy skills
    levels: tuple[str, ...]  # seniority levels named in the profile, lowest first
    categories: frozenset[str] = frozenset()  # type of work, from skill categories
    industries: tuple[str, ...] = ()


class JobFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    job: Job
    must_have: tuple[str, ...]  # skills named in the Requirements lines
    categories: Counter[str] = Counter()  # type of work: skill categories, counted
    industries: tuple[str, ...] = ()


class TitleMatch(BaseModel):
    """The contractor title closest to the job title."""

    cosine: float
    title: str


class Candidate(BaseModel):
    contractor_id: str
    cosine: float
    title_cosine: float | None
    matched_title: str | None
    skill_overlap: float | None
    score: float
    band: Band
    rank: int
    matched_skills: list[str]
    missing_must_have: list[str]
    gates_failed: list[str]
    flags: list[str]

    @property
    def flagged(self) -> bool:
        return bool(self.gates_failed)


def scale_cosine(cosine: float, floor: float, ceiling: float) -> float:
    return min(1.0, max(0.0, (cosine - floor) / (ceiling - floor)))


def skill_overlap(
    job_skills: list[str], skills: frozenset[str], cap: int
) -> tuple[float | None, list[str]]:
    """(job skills the contractor has / min(job's skill count, cap), those skills), at most 1.
    None if the job lists no skills."""
    if not job_skills:
        return None, []
    matched = [s for s in job_skills if s in skills]
    return min(1.0, len(matched) / min(len(job_skills), cap)), matched


_LOWER = {Band.HIGH: Band.MEDIUM, Band.MEDIUM: Band.LOW, Band.LOW: Band.LOW}


def band_for(score: float, bands: ConfidenceBands, *, missing_must_have: bool) -> Band:
    band = Band.HIGH if score >= bands.high else Band.MEDIUM if score >= bands.medium else Band.LOW
    return _LOWER[band] if missing_must_have else band


def score_pair(
    job: JobFacts,
    contractor: ContractorFacts,
    cosine: float,
    settings: MatchingSettings,
    title: TitleMatch | None = None,
) -> Candidate:
    overlap, matched = skill_overlap(job.job.skills, contractor.skills, settings.skill_overlap_cap)
    w = settings.weights
    parts: list[tuple[float, float]] = []  # (weight, value in 0..1)
    if title is not None:
        parts.append(
            (w.title, scale_cosine(title.cosine, settings.title_floor, settings.title_ceiling))
        )
    if overlap is not None:
        parts.append((w.skill_overlap, overlap))
    field = field_overlap(job.categories, contractor.categories)
    if field is not None:
        parts.append((w.field, field))
    skip_text = settings.thin_profiles_skip_text and is_thin(contractor.profile) and parts
    if not skip_text:  # a thin profile with no title or skills to compare still uses its text
        parts.append(
            (w.cosine, scale_cosine(cosine, settings.cosine_floor, settings.cosine_ceiling))
        )
    total = sum(weight for weight, _ in parts)
    score = sum(weight * value for weight, value in parts) / total if total else 0.0
    missing = [s for s in job.must_have if s not in contractor.skills]
    lower = bool(missing) and settings.missing_must_have_lowers_band
    gates = [
        reason
        for reason in (
            seniority_check(job.job.seniority, list(contractor.levels), settings.seniority_max_gap),
            location_check(job.job, contractor.profile, settings),
        )
        if reason
    ]
    return Candidate(
        contractor_id=contractor.profile.talent_id,
        cosine=round(cosine, 4),
        title_cosine=None if title is None else round(title.cosine, 4),
        matched_title=None if title is None else title.title,
        skill_overlap=None if overlap is None else round(overlap, 4),
        score=round(score, 4),
        band=band_for(score, settings.bands, missing_must_have=lower),
        rank=0,  # set by shortlist()
        matched_skills=matched,
        missing_must_have=missing,
        gates_failed=gates,
        flags=[
            *flags(job.job, contractor.profile, settings),
            *(
                note
                for note in (
                    field_note(job.categories, contractor.categories),
                    industry_note(job.industries, contractor.industries),
                )
                if note
            ),
        ],
    )


def rank_all(
    job: JobFacts,
    contractors: list[ContractorFacts],
    cosines: np.ndarray,
    settings: MatchingSettings,
    titles: list[TitleMatch | None] | None = None,
) -> list[Candidate]:
    """Every contractor scored for this job, best first, rank 1..n. titles[i] is contractor
    i's closest title, or None."""
    titles = titles if titles is not None else [None] * len(contractors)
    scored = sorted(
        (
            score_pair(job, c, float(cos), settings, title)
            for c, cos, title in zip(contractors, cosines, titles, strict=True)
        ),
        key=lambda c: (-c.score, c.contractor_id),
    )
    return [c.model_copy(update={"rank": rank}) for rank, c in enumerate(scored, 1)]


def select(ranked: list[Candidate], settings: MatchingSettings) -> list[Candidate]:
    """The shortlist from rank_all(): up to top_k contractors that pass every check and
    score at least min_score, plus any flagged contractor ranked above the last of them, so
    a flagged match shows where it would have stood."""
    picked: list[Candidate] = []
    passed = 0
    for candidate in ranked:
        if candidate.score < settings.min_score or passed == settings.top_k:
            break
        picked.append(candidate)
        passed += not candidate.flagged
    return picked


def shortlist(
    job: JobFacts,
    contractors: list[ContractorFacts],
    cosines: np.ndarray,
    settings: MatchingSettings,
    titles: list[TitleMatch | None] | None = None,
) -> list[Candidate]:
    return select(rank_all(job, contractors, cosines, settings, titles), settings)
