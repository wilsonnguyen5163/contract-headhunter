"""Matcher vs human reviews (match/evaluate.py), with hand-made rankings (no model)."""

from collections.abc import Callable
from typing import Any

import pytest

from jobmatch.config import MatchingSettings, Settings
from jobmatch.match.evaluate import evaluate, spearman
from jobmatch.match.run import ScoredJobs
from jobmatch.match.scorer import Candidate
from jobmatch.schemas import Band, ContractorProfile, Job, ReviewedMatch
from jobmatch.sources.paste import canonical_url

URL = "https://jobs.example.org/content-producer-12-month-contract"


def candidate(talent: str, score: float, rank: int, **fields: Any) -> Candidate:
    base: dict[str, Any] = {
        "contractor_id": talent,
        "cosine": 0.7,
        "title_cosine": None,
        "matched_title": None,
        "skill_overlap": None,
        "score": score,
        "band": Band.HIGH if score >= 0.7 else Band.MEDIUM if score >= 0.5 else Band.LOW,
        "rank": rank,
        "matched_skills": [],
        "missing_must_have": [],
        "gates_failed": [],
        "flags": [],
    }
    base.update(fields)
    return Candidate(**base)


def label(
    match_id: str, talent: str, tier: str, score: float, skills: float, **fields: Any
) -> ReviewedMatch:
    base: dict[str, Any] = {
        "match_id": match_id,
        "talent_id": talent,
        "engagement": "Fixed-term - 12 months",
        "posting_url": URL,
        "role": "Content Producer",
        "match_tier": tier,
        "match_score": score,
        "skills": skills,
    }
    base.update(fields)
    return ReviewedMatch(**base)


@pytest.fixture
def matching(settings: Settings) -> MatchingSettings:
    return settings.matching.model_copy(update={"top_k": 2, "min_score": 0.3})


@pytest.fixture
def scored(make_job: Callable[..., Job]) -> ScoredJobs:
    _, source_id, url = canonical_url(URL)
    job = make_job(job_id=f"manual:{source_id}", source="manual", source_id=source_id, url=url)
    profiles = [
        ContractorProfile(
            talent_id=t, talent_name=f"Sample {t}", rate_expectation="Industry Standard"
        )
        for t in ("A", "B", "C", "D")
    ]
    ranked = [
        candidate("A", 0.80, 1),
        candidate(
            "B",
            0.60,
            2,
            gates_failed=["seniority: under-qualified (job senior, contractor junior)"],
        ),
        candidate("C", 0.55, 3),
        candidate("D", 0.20, 4),
    ]
    return ScoredJobs(jobs=[job], profiles=profiles, ranked={job.job_id: ranked}, version="v")


def test_spearman() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert spearman([1, 2], [1, 2]) is None  # too few pairs
    assert spearman([1, 1, 1], [1, 2, 3]) is None  # no variation
    assert spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.949, abs=1e-3)  # ties averaged


def test_pairs_are_compared_or_explained(scored: ScoredJobs, matching: MatchingSettings) -> None:
    labels = [
        label("M-1", "A", "A", 90, 28),
        label("M-2", "B", "B", 80, 27),
        label("M-3", "D", "A", 88, 29),  # we rank D last: a miss
        label("M-4", "C", "reject", 60, 25),
        label("M-5", "Z", "A", 90, 30),  # not in our contractor list
        label("M-6", "A", "A", 90, 30, posting_url="https://jobs.example.org/other"),
        label("M-7", "A", "B", 75, 25, posting_url=None),
    ]
    report = evaluate(labels, scored, matching)
    status = {p.match_id: p.status for p in report.pairs}
    assert status["M-5"] == "talent not in the contractor list"
    assert status["M-6"] == "posting not an active job in jobs.csv"
    assert status["M-7"] == "no posting url"
    assert report.compared == 4

    by_id = {p.match_id: p for p in report.pairs}
    assert (by_id["M-1"].our_rank, by_id["M-1"].shortlisted, by_id["M-1"].contractors) == (
        1,
        True,
        4,
    )
    assert by_id["M-2"].flagged and by_id["M-2"].shortlisted
    assert not by_id["M-3"].shortlisted
    assert report.tier_band == {
        "A": {"high": 1, "not shortlisted": 1},
        "B": {"flagged": 1},
        "reject": {"medium": 1},
    }
    (job,) = report.recall
    assert (job.good, job.good_shortlisted, job.other_shortlisted) == (3, 2, 1)
    # Our order A > B > C > D against reviewer totals 90, 80, 60, 88.
    assert report.spearman_total == pytest.approx(0.4)
