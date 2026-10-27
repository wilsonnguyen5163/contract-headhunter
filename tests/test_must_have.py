"""Must-have skills (normalize/must_have.py) and the media/content skill terms added on
2026-10-26. Requirement lines are from real postings or written in their style."""

import pytest

from conftest import CONFIG_DIR
from jobmatch.config import load_normalization_rules, load_skills_taxonomy
from jobmatch.normalize.must_have import MustHaveReader
from jobmatch.normalize.skills import SkillExtractor

SKILLS = SkillExtractor(load_skills_taxonomy(CONFIG_DIR))
READER = MustHaveReader(SKILLS, load_normalization_rules(CONFIG_DIR).must_have_soft_markers)


@pytest.mark.parametrize(
    "line",
    [
        "Familiarity with digital communication platforms and technologies, particularly HTML5",
        "Experience with digital asset management and trafficking preferred, but not required",
        "Copy editing experience, particularly using CBC style, is a plus",
        "Knowledge of WordPress is considered an asset",
        "La maîtrise de WordPress constitue un atout",
        "Une expérience en montage vidéo est souhaitée",
        "Connaissances de base en Google Analytics",
    ],
)
def test_soft_lines_are_not_must_haves(line: str) -> None:
    assert READER.is_soft(line)
    assert READER.read([line]) == []


def test_firm_lines_give_must_haves_in_order() -> None:
    lines = [
        "Experience and deep understanding with graphic design, web design, prototyping",
        "Good understanding of project management and strong organizational skills",
        "Familiarity with HTML5 and mobile",  # soft: html is not a must-have
        "Expérience en gestion de projet",  # repeats project management
    ]
    assert READER.read(lines) == [
        "graphic design",
        "web design",
        "ui/ux design",
        "project management",
    ]


def test_digital_asset_management_alone_is_not_soft() -> None:
    # Bare "asset" is deliberately not a soft marker.
    line = "Experience with digital asset management systems"
    assert not READER.is_soft(line)
    assert READER.read([line]) == ["digital asset management"]


@pytest.mark.parametrize(
    ("text", "skills"),
    [
        (
            "Creates and maintains short, medium and long-term editorial schedules",
            ["editorial planning"],
        ),
        ("Gère le calendrier éditorial et la planification éditoriale", ["editorial planning"]),
        ("Copy editing and proofreading of all web content", ["copy editing"]),
        ("In-depth knowledge of lifestyle journalism", ["journalism"]),
        ("Journaliste avec expérience en salle de nouvelles", ["journalism"]),
        ("Integrated Producer", ["digital production"]),
        (
            "Productrice numérique, campagnes intégrées",
            ["digital production", "integrated marketing"],
        ),
        (
            "Video editing in Premiere; motion graphics in After Effects",
            ["video production", "motion graphics"],
        ),
        ("Montage vidéo et graphisme animé", ["video production", "motion graphics"]),
        (
            "Must be comfortable publishing in content management systems",
            ["content management systems"],
        ),
        ("Monitors traffic in Google Analytics", ["web analytics"]),
        ("Sr. Art Director/Designer", ["art direction"]),
        ("Directrice de création", ["creative direction"]),
        ("Senior Content Strategist", ["content strategy"]),
        (
            "Project Coordinator, Media Planner & Analytics Specialist",
            ["project management", "media planning"],
        ),
    ],
)
def test_new_media_skills(text: str, skills: list[str]) -> None:
    assert SKILLS.extract(text) == skills


@pytest.mark.parametrize(
    "text",
    [
        "Under the direction of the Managing Editor",  # "editor" alone is a role, not a skill
        "Senior Producer, CBC Life",  # bare "producer" is left to title similarity
        "Excellent analytical and numeracy skills",
        "Tracks spending against budget with the Production Manager",
    ],
)
def test_ambiguous_words_stay_out(text: str) -> None:
    assert not {
        "copy editing",
        "digital production",
        "web analytics",
        "content production",
    } & set(SKILLS.extract(text))
