import pytest

from conftest import CONFIG_DIR
from jobmatch.config import load_canada_locations
from jobmatch.normalize.work_mode import WorkModeDetector, resolve
from jobmatch.schemas import RemoteType

DETECTOR = WorkModeDetector(load_canada_locations(CONFIG_DIR))
REMOTE, HYBRID = RemoteType.REMOTE, RemoteType.HYBRID
ONSITE, UNKNOWN = RemoteType.ONSITE, RemoteType.UNKNOWN


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ([], UNKNOWN),
        ([HYBRID], HYBRID),
        ([HYBRID, REMOTE], REMOTE),  # hybrid + remote -> remote
        ([REMOTE, HYBRID], REMOTE),
        ([HYBRID, ONSITE], ONSITE),  # hybrid + in person -> in person
        ([ONSITE, HYBRID, HYBRID], ONSITE),
        ([REMOTE, ONSITE], REMOTE),  # both extremes -> first seen
        ([ONSITE, REMOTE], ONSITE),
        ([HYBRID, ONSITE, REMOTE], ONSITE),
    ],
)
def test_precedence(signals: list[RemoteType], expected: RemoteType) -> None:
    assert resolve(signals) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # in person
        ("On-site", ONSITE),
        ("In person, Calgary", ONSITE),
        ("Office-based role", ONSITE),
        ("Sur place", ONSITE),
        ("En présentiel au bureau", ONSITE),
        # hybrid
        ("Hybrid", HYBRID),
        ("Mixed", HYBRID),
        ("Partially remote", HYBRID),
        ("Mode hybride", HYBRID),
        ("Télétravail partiel", HYBRID),  # longest term wins: not "télétravail" = remote
        ("3 days a week in the office", HYBRID),  # pattern, so "in the office" is not on-site
        ("2 jours par semaine au bureau", HYBRID),
        # remote
        ("Remote", REMOTE),
        ("Work from home", REMOTE),
        ("Home-based", REMOTE),
        ("Telework", REMOTE),
        ("100% télétravail", REMOTE),
        ("Travail à distance", REMOTE),
        # conflicts
        ("Hybrid or remote", REMOTE),
        ("Hybride / sur place", ONSITE),
        ("Remote or on-site", REMOTE),
        ("On-site or remote", ONSITE),
        # nothing
        ("Toronto, ON", UNKNOWN),
        ("Virtual Assistant", UNKNOWN),  # "virtual" alone is left out on purpose
        ("Soins à domicile", UNKNOWN),
    ],
)
def test_detect(text: str, expected: RemoteType) -> None:
    assert DETECTOR.detect(text) is expected


def test_patterns_only_mode_ignores_terms() -> None:
    sentence = "Remote monitoring of client servers is part of the work, 2 days a week on site"
    assert DETECTOR.signals(sentence, terms=False) == [HYBRID]
