"""Type of work (skill categories) and industry notes (match/fields.py). Synthetic text."""

from collections import Counter
from collections.abc import Callable

import pytest

from conftest import CONFIG_DIR
from jobmatch.config import (
    MatchingSettings,
    MatchingWeights,
    Settings,
    load_industries,
    load_skills_taxonomy,
)
from jobmatch.match.fields import (
    IndustryFinder,
    field_note,
    field_overlap,
    industry_note,
    main_field,
    skill_categories,
)
from jobmatch.match.scorer import ContractorFacts, JobFacts, score_pair
from jobmatch.schemas import ContractorProfile, Job

TAXONOMY = load_skills_taxonomy(CONFIG_DIR)
FINDER = IndustryFinder(load_industries(CONFIG_DIR))
IGNORE = ["management", "business", "learned"]


def test_categories_ignore_cross_functional_skills() -> None:
    skills = [
        "product marketing",
        "demand generation",
        "business development",
        "project management",
        "not a skill",
    ]
    assert skill_categories(skills, TAXONOMY, IGNORE) == Counter({"marketing": 2, "sales": 1})


def test_field_overlap_and_note() -> None:
    job = Counter({"marketing": 3, "sales": 1})
    assert field_overlap(job, frozenset({"marketing"})) == 0.75
    assert field_overlap(job, frozenset({"software"})) == 0.0
    assert field_overlap(Counter(), frozenset({"marketing"})) is None
    assert field_overlap(job, frozenset()) is None
    assert main_field(job) == "marketing"
    assert (
        field_note(job, frozenset({"sales", "media"}))
        == "field: job marketing · contractor media, sales"
    )
    assert field_note(job, frozenset({"marketing", "sales"})) is None


@pytest.mark.parametrize(
    ("text", "industries"),
    [
        (
            "Ross Video powers live production for sports and entertainment broadcasters.",
            ("media and entertainment", "sports"),
        ),
        (
            "Organisme à but non lucratif en santé numérique",
            ("healthcare and life sciences", "nonprofit"),
        ),
        ("A B2B SaaS company in fintech", ("financial services", "technology and saas")),
        ("We value company culture, energy and social media savvy.", ()),  # generic words
    ],
)
def test_job_industries(text: str, industries: tuple[str, ...]) -> None:
    assert FINDER.in_job(text) == industries


def test_contractor_industries_use_short_labels_too() -> None:
    assert FINDER.in_contractor(["Agencies", "technology", "Health", "Cleantech"]) == (
        "agencies and advertising",
        "technology and saas",
        "healthcare and life sciences",
        "cleantech and energy",
    )


def test_industry_note() -> None:
    assert (
        industry_note(("sports",), ("sports", "media and entertainment")) == "same industry: sports"
    )
    assert (
        industry_note(("media and entertainment", "sports"), ("cleantech and energy",))
        == "industry: job media and entertainment, sports · contractor cleantech and energy"
    )
    assert industry_note((), ("sports",)) is None
    assert industry_note(("sports",), ()) is None


def test_field_part_and_notes_in_the_score(
    settings: Settings, make_job: Callable[..., Job]
) -> None:
    matching: MatchingSettings = settings.matching.model_copy(
        update={"weights": MatchingWeights(cosine=0.5, title=0, skill_overlap=0, field=0.5)}
    )
    job = JobFacts(
        job=make_job(),
        must_have=(),
        categories=Counter({"marketing": 2}),
        industries=("sports",),
    )
    profile = ContractorProfile(
        talent_id="A",
        talent_name="Sample A",
        rate_expectation="Industry Standard",
        executive_summary="x",
    )
    seller = ContractorFacts(
        profile=profile,
        skills=frozenset(),
        levels=(),
        categories=frozenset({"sales"}),
        industries=("cleantech and energy",),
    )
    marketer = seller.model_copy(
        update={"categories": frozenset({"marketing"}), "industries": ("sports",)}
    )
    s, m = score_pair(job, seller, 0.80, matching), score_pair(job, marketer, 0.80, matching)
    assert (s.score, m.score) == (0.5, 1.0)  # same text; field covers 0 vs all of the job
    assert "field: job marketing · contractor sales" in s.flags
    assert "industry: job sports · contractor cleantech and energy" in s.flags
    assert "same industry: sports" in m.flags
    assert not any(f.startswith("field:") for f in m.flags)


def test_repo_settings_weight_field_lightly(settings: Settings) -> None:
    assert 0 < settings.matching.weights.field <= settings.matching.weights.skill_overlap
    assert "management" in settings.matching.field_ignore_categories


def test_degree_requirements_are_not_the_education_industry() -> None:
    text = "Minimum education: Bachelor's degree. A university degree in marketing is required."
    assert FINDER.in_job(text) == ()
    assert FINDER.in_contractor(["Education", "University"]) == ("education",)


def test_event_logistics_is_not_the_logistics_industry() -> None:
    assert (
        FINDER.in_job("Align on objectives, audiences, logistics, deadlines and deliverables.")
        == ()
    )
    assert FINDER.in_contractor(["Logistics"]) == ("transportation and logistics",)
