"""Scoring, bands and the shortlist (match/scorer.py). Profiles are synthetic."""

from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from jobmatch.config import ConfidenceBands, MatchingSettings, MatchingWeights, Settings
from jobmatch.match.scorer import (
    ContractorFacts,
    JobFacts,
    TitleMatch,
    band_for,
    scale_cosine,
    score_pair,
    shortlist,
    skill_overlap,
)
from jobmatch.schemas import Band, ContractorProfile, Job


def facts(
    talent_id: str, skills: set[str] = frozenset(), levels: tuple[str, ...] = (), **fields: Any
) -> ContractorFacts:
    base: dict[str, Any] = {
        "talent_id": talent_id,
        "talent_name": f"Sample {talent_id}",
        "executive_summary": "SYNTHETIC.",
        "rate_expectation": "Industry Standard",
    }
    p = ContractorProfile(**{**base, **fields})
    return ContractorFacts(profile=p, skills=frozenset(skills), levels=levels)


@pytest.fixture
def matching(settings: Settings) -> MatchingSettings:
    return settings.matching.model_copy(
        update={
            "top_k": 2,
            "min_score": 0.3,
            "cosine_floor": 0.45,
            "cosine_ceiling": 0.80,
            "title_floor": 0.50,
            "title_ceiling": 0.85,
            "weights": MatchingWeights(cosine=0.7, title=0.3, skill_overlap=0.3),
            "bands": ConfidenceBands(high=0.7, medium=0.5),
            "missing_must_have_lowers_band": True,
        }
    )


def test_cosine_is_stretched_and_clamped() -> None:
    assert scale_cosine(0.45, 0.45, 0.80) == 0
    assert scale_cosine(0.625, 0.45, 0.80) == pytest.approx(0.5)
    assert scale_cosine(0.95, 0.45, 0.80) == 1
    assert scale_cosine(0.10, 0.45, 0.80) == 0


def test_skill_overlap_is_coverage_of_the_job() -> None:
    have = frozenset({"project management", "copywriting", "seo", "html"})
    assert skill_overlap(["project management", "graphic design"], have, 4) == (
        0.5,
        ["project management"],
    )
    assert skill_overlap([], have, 4) == (None, [])  # nothing to compare


def test_skill_overlap_is_capped_for_long_tag_lists() -> None:
    job = [
        "digital production", "marketing strategy", "web design", "project management",
        "graphic design", "ui/ux design", "motion graphics", "content production",
        "content management systems", "editorial planning", "digital asset management", "html",
    ]  # fmt: skip
    three = frozenset({"digital production", "project management", "html"})
    assert skill_overlap(job, three, 4)[0] == 0.75  # 3 of a 4-skill core, not 3 of 12
    assert skill_overlap(job, frozenset(job), 4)[0] == 1.0  # never above 1
    assert skill_overlap(job[:2], three, 4)[0] == 0.5  # short lists are unaffected


def test_band_steps_down_for_a_missing_must_have() -> None:
    bands = ConfidenceBands(high=0.7, medium=0.5)
    assert band_for(0.75, bands, missing_must_have=False) == Band.HIGH
    assert band_for(0.75, bands, missing_must_have=True) == Band.MEDIUM
    assert band_for(0.55, bands, missing_must_have=True) == Band.LOW
    assert band_for(0.2, bands, missing_must_have=True) == Band.LOW


def test_score_combines_cosine_and_skills(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = JobFacts(
        job=make_job(skills=["project management", "graphic design"], seniority="senior"),
        must_have=("graphic design",),
    )
    c = score_pair(job, facts("A", {"project management"}, ("senior",)), 0.66, matching)
    # 0.7 * 0.6 (cosine 0.66 stretched) + 0.3 * 0.5 (one of two skills)
    assert c.score == pytest.approx(0.57)
    assert c.matched_skills == ["project management"]
    assert c.missing_must_have == ["graphic design"]
    assert c.band == Band.LOW  # medium, one step down for the missing must-have
    assert not c.flagged


def test_job_without_skills_is_scored_on_cosine(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = JobFacts(
        job=make_job(title_raw="Rédacteur ou rédactrice (contrat de 6 mois)"), must_have=()
    )
    c = score_pair(job, facts("A"), 0.80, matching)
    assert (c.score, c.skill_overlap, c.band) == (1.0, None, Band.HIGH)


def test_failed_check_marks_the_candidate(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = JobFacts(job=make_job(seniority="executive"), must_have=())
    c = score_pair(job, facts("A", levels=("junior",)), 0.9, matching)
    assert c.flagged
    assert c.gates_failed == ["seniority: under-qualified (job executive, contractor junior)"]


def test_shortlist_keeps_top_k_passing_and_flagged_ranked_above_them(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = JobFacts(job=make_job(seniority="senior"), must_have=())
    contractors = [
        facts("A", levels=("senior",)),
        facts("B", levels=("junior",)),  # flagged: under-qualified
        facts("C", levels=("lead",)),
        facts("D", levels=("senior",)),
        facts("E"),  # below min_score
    ]
    cosines = np.array([0.80, 0.78, 0.70, 0.60, 0.40])
    picked = shortlist(job, contractors, cosines, matching)
    assert [(c.contractor_id, c.rank, c.flagged) for c in picked] == [
        ("A", 1, False),
        ("B", 2, True),
        ("C", 3, False),
    ]  # top_k = 2 passing; D (4th) is left out, E is under min_score


def test_shortlist_can_be_empty(make_job: Callable[..., Job], matching: MatchingSettings) -> None:
    job = JobFacts(job=make_job(), must_have=())
    assert shortlist(job, [facts("A")], np.array([0.2]), matching) == []


def test_title_joins_the_weighted_average(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = JobFacts(job=make_job(skills=["project management", "graphic design"]), must_have=())
    contractor = facts("A", {"project management"})
    title = TitleMatch(cosine=0.85, title="integrated producer")
    c = score_pair(job, contractor, 0.66, matching, title)
    # (0.7 * 0.6 + 0.3 * 1.0 + 0.3 * 0.5) / 1.3
    assert c.score == pytest.approx(0.6692, abs=1e-4)
    assert (c.title_cosine, c.matched_title) == (0.85, "integrated producer")
    # A thin profile with a close title now outranks the same profile without one.
    assert c.score > score_pair(job, contractor, 0.66, matching).score


def test_must_have_band_step_can_be_turned_off(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = JobFacts(job=make_job(skills=["graphic design"]), must_have=("graphic design",))
    on = score_pair(job, facts("A"), 0.80, matching)
    off = score_pair(
        job, facts("A"), 0.80, matching.model_copy(update={"missing_must_have_lowers_band": False})
    )
    assert on.missing_must_have == off.missing_must_have == ["graphic design"]  # always shown
    assert (on.band, off.band) == (Band.MEDIUM, Band.HIGH)


def test_thin_profile_is_scored_on_title_and_skills(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = JobFacts(job=make_job(skills=["digital production"]), must_have=())
    thin = facts("ON-028", {"digital production"}, executive_summary=None, core_strengths=[])
    title = TitleMatch(cosine=0.85, title="integrated producer")
    # A weak full-text cosine (0.50) is ignored: (0.3 * 1.0 + 0.3 * 1.0) / 0.6
    assert score_pair(job, thin, 0.50, matching, title).score == 1.0
    off = matching.model_copy(update={"thin_profiles_skip_text": False})
    assert score_pair(job, thin, 0.50, off, title).score < 1.0
    # A full profile keeps its text part.
    full = facts("A", {"digital production"})
    assert score_pair(job, full, 0.50, matching, title).score < 1.0


def test_thin_profile_with_nothing_else_still_uses_its_text(
    make_job: Callable[..., Job], matching: MatchingSettings
) -> None:
    job = JobFacts(job=make_job(), must_have=())  # no skills, and no title vector given
    thin = facts("ON-014", executive_summary=None, core_strengths=[])
    assert score_pair(job, thin, 0.80, matching).score == 1.0
