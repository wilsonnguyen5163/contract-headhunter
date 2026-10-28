"""Skills: the 2026-10-26 curated additions, skills learned from the contractor list, and
the loader that merges both. Profiles are synthetic."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from conftest import CONFIG_DIR
from jobmatch.config import load_skills_taxonomy
from jobmatch.normalize.pipeline import Normalizer
from jobmatch.normalize.skill_learning import (
    learn_skills,
    learnable,
    load_skill_learning,
    write_learned,
)
from jobmatch.normalize.skills import SkillExtractor
from jobmatch.schemas import ContractorProfile, RawJob

CURATED = load_skills_taxonomy(CONFIG_DIR)
SKILLS = SkillExtractor(CURATED)
RULES = load_skill_learning(CONFIG_DIR)


def profile(talent_id: str, strengths: list[str]) -> ContractorProfile:
    return ContractorProfile(
        talent_id=talent_id,
        talent_name=f"Sample {talent_id}",
        rate_expectation="Industry Standard",
        core_strengths=strengths,
    )


# ---- curated additions ----


@pytest.mark.parametrize(
    ("text", "skills"),
    [
        ("B2B product marketing and GTM strategy", ["product marketing", "go-to-market"]),
        ("Mise en marché et marketing de produit", ["go-to-market", "product marketing"]),
        (
            "ABM programs, demand gen and lead generation",
            ["account-based marketing", "demand generation", "lead generation"],
        ),
        (
            "Génération de prospects et marketing événementiel",
            ["lead generation", "event marketing"],
        ),
        ("Brand positioning and brand identity", ["positioning", "brand strategy"]),
        ("Growth experimentation and CRO", ["growth marketing", "conversion rate optimization"]),
        ("Outbound prospecting and pipeline management", ["consultative selling"]),
        (
            "Gestion de comptes clés et développement des affaires",
            ["account management", "business development"],
        ),
        ("UX research and user-centred design", ["ux research", "ui/ux design"]),
        ("People operations and HR strategy", ["human resources"]),
        ("Seed funding and investor relations", ["fundraising", "investor relations"]),
        ("Keyword research for SEO", ["seo"]),
        ("Fractional CTO and engineering leadership", ["engineering leadership"]),
        ("AI transformation roadmap", ["ai strategy"]),
    ],
)
def test_curated_marketing_sales_and_ops_skills(text: str, skills: list[str]) -> None:
    assert SKILLS.extract(text) == skills


@pytest.mark.parametrize(
    "text",
    [
        "J'ai géré des campagnes",  # "ai" must not match French "j'ai"
        "Strong writing and editing skills",
        "Design and operations support",
        "Analytics and reporting dashboards",
        "Event schedules and sales pipeline reviews",
    ],
)
def test_generic_words_tag_nothing_new(text: str) -> None:
    new = {
        "ai strategy", "technical writing", "operations management", "marketing analytics",
        "event marketing", "consultative selling", "editorial design",
    }  # fmt: skip
    assert not new & set(SKILLS.extract(text))


# ---- learning from the contractor list ----


@pytest.mark.parametrize(
    ("phrase", "ok"),
    [
        ("sows and change control", True),
        ("okr", True),  # an approved acronym
        ("operations", False),  # stopword
        ("quantum", False),  # a single word that is not an acronym
        ("a very long phrase with far too many words", False),
    ],
)
def test_learnable(phrase: str, ok: bool) -> None:
    assert learnable(phrase, RULES) is ok


def test_unknown_strengths_are_learned_and_known_ones_skipped() -> None:
    profiles = [
        profile("A", ["Product marketing", "SOWs and change control", "Operations", "OKR"]),
        profile("B", ["SOWs and change control", "Quantum technology", "Rédaction scientifique"]),
        profile("C", ["Redaction scientifique", "Growth marketng"]),  # accent variant, typo
    ]
    learned, suggestions, report = learn_skills(profiles, CURATED, RULES)
    assert sorted(learned.skills) == [
        "growth marketng",
        "okr",
        "quantum technology",
        "redaction scientifique",
        "sows and change control",
    ]
    assert all(e.category == "learned" for e in learned.skills.values())
    assert (report.known, report.learned) == (1, 5)  # product marketing is curated
    assert report.skipped == 2  # "operations"; the accented duplicate of "redaction scientifique"
    by_phrase = {s.phrase: s for s in suggestions}
    assert by_phrase["sows and change control"].contractors == 2
    assert by_phrase["growth marketng"].closest_skill == "growth marketing"  # merge hint


def test_learned_skills_load_with_the_taxonomy_and_curated_wins(tmp_path: Path) -> None:
    learned, _, _ = learn_skills([profile("A", ["SOWs and change control"])], CURATED, RULES)
    path = tmp_path / "skills_learned.yaml"
    write_learned(learned, path)
    assert path.read_text(encoding="utf-8").startswith("# Generated by build-contractors")
    merged = load_skills_taxonomy(CONFIG_DIR, path)
    assert "sows and change control" in merged.skills
    assert len(merged.skills) == len(CURATED.skills) + 1
    assert SkillExtractor(merged).extract("Manage SOWs and change control with clients") == [
        "sows and change control"
    ]

    clash = tmp_path / "clash.yaml"
    clash.write_text(
        "version: 1\nskills:\n  search optimisation: {category: learned, aliases: [seo]}\n",
        encoding="utf-8",
    )
    assert "search optimisation" not in load_skills_taxonomy(CONFIG_DIR, clash).skills
    assert load_skills_taxonomy(CONFIG_DIR, tmp_path / "missing.yaml") == CURATED


# ---- job titles from saved pages ----


@pytest.mark.parametrize(
    ("title", "norm"),
    [
        (
            "Marketing Manager - Sports & Entertainment (13-Month Contract) - Expired | Ross Video",
            "marketing manager - sports & entertainment",
        ),
        ("Rédactrice web (Expirée)", "rédactrice web"),
        ("Content Producer | Example Board", "content producer"),
        ("Senior Data Engineer", "senior data engineer"),
    ],
)
def test_board_suffixes_and_expired_markers_leave_titles(
    normalizer: Normalizer, title: str, norm: str
) -> None:
    fields: dict[str, Any] = {
        "source": "manual",
        "source_id": "url-1",
        "url": "https://jobs.example.org/1",
        "title": title,
        "fetched_at": datetime(2026, 10, 26, tzinfo=UTC),
    }
    job = normalizer.normalize(RawJob(**fields), now=datetime(2026, 10, 26, tzinfo=UTC))
    assert job.title_norm == norm


@pytest.mark.parametrize(
    ("text", "skills"),
    [
        ("Prepare Marketing Qualified Lead (MQL) reports", ["lead generation"]),
        (
            "Experience with webinar platforms such as Livestorm or GoToWebinar",
            ["webinar marketing"],
        ),
        ("Organiser des webinaires et des événements virtuels", ["webinar marketing"]),
    ],
)
def test_webinar_and_mql_skills(text: str, skills: list[str]) -> None:
    assert SKILLS.extract(text) == skills


def test_posting_without_found_headings_keeps_its_text_and_skills(normalizer: Normalizer) -> None:
    text = (
        "As a Marketing Specialist, Webinars, you will lead the planning of live webinars. "
        "What You'll Be Doing: Develop the webinar calendar. Prepare Marketing Qualified Lead "
        "(MQL) reports. What We're Looking For: Strong project management skills. Willingness "
        "to learn HubSpot and Jira.\n\nNice to Have:\n- Public speaking experience"
    )
    raw = RawJob(
        source="manual",
        source_id="url-2",
        url="https://jobs.example.org/2",
        title="Marketing Specialist, Webinars (14-Month Contract)",
        description=text,
        fetched_at=datetime(2026, 10, 26, tzinfo=UTC),
    )
    job = normalizer.normalize(raw, now=datetime(2026, 10, 26, tzinfo=UTC))
    assert {"webinar marketing", "lead generation", "project management", "crm", "jira"} <= set(
        job.skills
    )
    assert "Develop the webinar calendar" in (job.description_clean or "")
    assert job.nice_to_have == ["Public speaking experience"]
