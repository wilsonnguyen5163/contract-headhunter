"""Compare the matcher with human reviews (data/eval/reviewed_matches.csv).

For every reviewed (posting, talent) pair whose posting is an active job in jobs.csv and
whose talent is in the contractor list, the matcher's score, band and rank for that pair
are set beside the reviewers' score, tier and skills sub-score. Reported:

- rank correlation (Spearman) of our score with the reviewers' total and skills scores;
- tier vs band counts;
- for each job, how many of the reviewers' A and B talents made our shortlist.

The reviewers' total also scores availability, location, contract likelihood, posting age
and buyer urgency, which the matcher does not, so their skills sub-score and tier are the
fairer comparison. Pairs that cannot be compared are listed with the reason.
"""

from collections import Counter

import numpy as np
from pydantic import BaseModel

from jobmatch.config import MatchingSettings
from jobmatch.match.run import ScoredJobs
from jobmatch.match.scorer import Candidate, select
from jobmatch.schemas import ReviewedMatch
from jobmatch.sources.paste import PasteFormatError, canonical_url

GOOD_TIERS = ("A", "B")


class EvalPair(BaseModel):
    match_id: str
    talent_id: str
    job_id: str | None
    role: str | None
    human_score: float | None
    human_tier: str | None
    human_skills: float | None
    our_score: float | None = None
    our_band: str | None = None
    our_rank: int | None = None  # among all contractors for this job
    contractors: int | None = None
    shortlisted: bool = False
    flagged: bool = False
    status: str  # "compared" or why not


class JobRecall(BaseModel):
    job_id: str
    role: str | None
    good: int  # reviewers' A/B talents that are in our contractor list
    good_shortlisted: int
    other_shortlisted: int  # reviewers' C/reject talents we shortlisted


class EvalReport(BaseModel):
    pairs: list[EvalPair]
    compared: int
    spearman_total: float | None
    spearman_skills: float | None
    tier_band: dict[str, dict[str, int]]  # tier -> our band (or "flagged" / "not shortlisted")
    recall: list[JobRecall]


def _ranks(values: np.ndarray) -> np.ndarray:
    """1-based ranks, ties sharing their average rank."""
    order = values.argsort(kind="stable")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1)
    for value in np.unique(values):
        tied = values == value
        ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    """Rank correlation, or None with fewer than 3 pairs or no variation."""
    if len(a) < 3:
        return None
    ra, rb = _ranks(np.asarray(a, dtype=float)), _ranks(np.asarray(b, dtype=float))
    if ra.std() == 0 or rb.std() == 0:
        return None
    return round(float(np.corrcoef(ra, rb)[0, 1]), 3)


def _job_key(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return canonical_url(url)[1]
    except PasteFormatError:
        return None


def evaluate(
    labels: list[ReviewedMatch], scored: ScoredJobs, settings: MatchingSettings
) -> EvalReport:
    jobs_by_key = {j.source_id: j for j in scored.jobs}
    talents = {p.talent_id for p in scored.profiles}
    shortlists = {job_id: select(r, settings) for job_id, r in scored.ranked.items()}
    pairs: list[EvalPair] = []
    for label in labels:
        key = _job_key(label.posting_url)
        job = jobs_by_key.get(key) if key else None
        pair = EvalPair(
            match_id=label.match_id,
            talent_id=label.talent_id,
            job_id=job.job_id if job else None,
            role=label.role,
            human_score=label.match_score,
            human_tier=label.match_tier,
            human_skills=label.skills,
            status="compared",
        )
        if key is None:
            pair.status = "no posting url"
        elif job is None:
            pair.status = "posting not an active job in jobs.csv"
        elif label.talent_id not in talents:
            pair.status = "talent not in the contractor list"
        else:
            ranked = scored.ranked[job.job_id]
            ours: Candidate = next(c for c in ranked if c.contractor_id == label.talent_id)
            pair.our_score = ours.score
            pair.our_band = ours.band.value
            pair.our_rank = ours.rank
            pair.contractors = len(ranked)
            pair.flagged = ours.flagged
            pair.shortlisted = any(
                c.contractor_id == label.talent_id for c in shortlists[job.job_id]
            )
        pairs.append(pair)

    compared = [p for p in pairs if p.status == "compared"]
    with_total = [p for p in compared if p.human_score is not None]
    with_skills = [p for p in compared if p.human_skills is not None]

    tier_band: dict[str, Counter[str]] = {}
    for p in compared:
        if not p.shortlisted:
            outcome = "not shortlisted"
        elif p.flagged:
            outcome = "flagged"
        else:
            outcome = p.our_band or "?"
        tier_band.setdefault(p.human_tier or "none", Counter())[outcome] += 1

    recall: list[JobRecall] = []
    for job_id in dict.fromkeys(p.job_id for p in compared if p.job_id):
        here = [p for p in compared if p.job_id == job_id]
        good = [p for p in here if p.human_tier in GOOD_TIERS]
        recall.append(
            JobRecall(
                job_id=job_id,
                role=here[0].role,
                good=len(good),
                good_shortlisted=sum(p.shortlisted for p in good),
                other_shortlisted=sum(p.shortlisted for p in here if p not in good),
            )
        )

    return EvalReport(
        pairs=pairs,
        compared=len(compared),
        spearman_total=spearman(
            [p.our_score for p in with_total],  # type: ignore[misc]
            [p.human_score for p in with_total],  # type: ignore[misc]
        ),
        spearman_skills=spearman(
            [p.our_score for p in with_skills],  # type: ignore[misc]
            [p.human_skills for p in with_skills],  # type: ignore[misc]
        ),
        tier_band={tier: dict(counts) for tier, counts in sorted(tier_band.items())},
        recall=recall,
    )
